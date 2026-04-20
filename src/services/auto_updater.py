"""Auto-updater : check les releases GitHub publiques et applique les mises à jour.

Flow :
    1. Lit la version locale depuis `VERSION` à la racine du projet
    2. Fetch la dernière release publique via l'API GitHub (pas d'auth requise)
    3. Compare les versions (semver simple)
    4. Si nouvelle version disponible, l'utilisateur peut :
       - Télécharger le zip de la release
       - L'extraire dans un dossier temporaire
       - Appliquer les fichiers (remplace code, préserve data/user_prefs.json & .env)
       - Demander un redémarrage

Configuration :
    GITHUB_REPO est défini comme "OWNER/REPO" (à configurer quand le repo existe).
    Pour tester sans repo : mettre GITHUB_REPO = "" désactive l'updater.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# CONFIGURATION : repo GitHub où les releases sont publiées
# Format : "utilisateur/repo"
GITHUB_REPO = "ImSombre/dofus-bot"
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

# Fichiers/dossiers à NE PAS écraser lors d'une mise à jour
PRESERVE_PATHS = [
    "data/user_prefs.json",
    "data/bot.sqlite3",
    "data/templates",
    "data/calibration",
    "data/tessdata",
    "data/vision_debug",
    "data/ocr_debug",
    "logs",
    "screenshots",
    ".env",
    ".venv",
]


@dataclass
class UpdateInfo:
    """Résultat du check de mise à jour."""
    has_update: bool
    current_version: str
    latest_version: str = ""
    release_name: str = ""
    release_notes: str = ""
    zip_url: str = ""
    error: str = ""


def get_current_version(project_root: Path | None = None) -> str:
    """Lit le fichier VERSION à la racine du projet."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent
    version_file = project_root / "VERSION"
    if not version_file.exists():
        return "0.0.0"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse '1.2.3' → (1, 2, 3). Supporte 'v1.2.3' aussi."""
    v = v.lstrip("v").strip()
    try:
        return tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


def check_for_update(repo: str | None = None) -> UpdateInfo:
    """Query l'API GitHub pour la dernière release.

    Retourne UpdateInfo avec has_update=True si une version plus récente existe.
    """
    current = get_current_version()
    repo = repo or GITHUB_REPO

    if not repo:
        return UpdateInfo(
            has_update=False,
            current_version=current,
            error="GITHUB_REPO non configuré (repo pas encore créé)",
        )

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return UpdateInfo(
            has_update=False,
            current_version=current,
            error="Module 'requests' non installé",
        )

    try:
        url = GITHUB_API.format(repo=repo)
        r = requests.get(url, timeout=6.0, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            return UpdateInfo(
                has_update=False,
                current_version=current,
                error=f"Repo '{repo}' introuvable ou pas de release",
            )
        if r.status_code != 200:
            return UpdateInfo(
                has_update=False,
                current_version=current,
                error=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        data = r.json()
    except Exception as exc:
        return UpdateInfo(
            has_update=False,
            current_version=current,
            error=f"Erreur réseau : {exc}",
        )

    tag = data.get("tag_name", "")
    latest = tag.lstrip("v")
    release_name = data.get("name") or tag
    notes = data.get("body", "")

    # Cherche l'asset zip à télécharger
    zip_url = ""
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".zip"):
            zip_url = asset.get("browser_download_url", "")
            break
    if not zip_url:
        # Fallback : source zipball
        zip_url = data.get("zipball_url", "")

    has_update = _parse_version(latest) > _parse_version(current)

    return UpdateInfo(
        has_update=has_update,
        current_version=current,
        latest_version=latest,
        release_name=release_name,
        release_notes=notes,
        zip_url=zip_url,
    )


def download_and_apply_update(
    info: UpdateInfo,
    project_root: Path | None = None,
    progress_cb=None,
) -> tuple[bool, str]:
    """Télécharge le zip et remplace les fichiers du projet.

    Préserve les fichiers listés dans PRESERVE_PATHS.
    Retourne (success, message).
    """
    if not info.has_update or not info.zip_url:
        return False, "Pas de mise à jour disponible"

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return False, "Module 'requests' non installé"

    # 1. Télécharge le zip
    def _progress(stage: str, pct: float = 0.0):
        if progress_cb:
            try:
                progress_cb(stage, pct)
            except Exception:
                pass

    _progress("Téléchargement...", 0.0)
    try:
        r = requests.get(info.zip_url, timeout=90.0, stream=True)
        if r.status_code != 200:
            return False, f"Download HTTP {r.status_code}"
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        buf = io.BytesIO()
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                buf.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    _progress("Téléchargement...", downloaded / total * 0.5)
        buf.seek(0)
    except Exception as exc:
        return False, f"Erreur download : {exc}"

    # 2. Extrait dans un dossier temporaire
    _progress("Extraction...", 0.5)
    tmp_dir = Path(tempfile.mkdtemp(prefix="dofus-bot-update-"))
    try:
        with zipfile.ZipFile(buf) as zf:
            zf.extractall(tmp_dir)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"Erreur extraction : {exc}"

    # 3. Trouve le dossier racine extrait (souvent `repo-name-hash/...`)
    extracted_root = tmp_dir
    children = list(tmp_dir.iterdir())
    if len(children) == 1 and children[0].is_dir():
        extracted_root = children[0]

    # 4. Copie les fichiers vers le projet (en respectant PRESERVE_PATHS)
    _progress("Application des fichiers...", 0.75)
    preserve_set = {p.replace("\\", "/").lower().rstrip("/") for p in PRESERVE_PATHS}

    def _is_preserved(rel_path: str) -> bool:
        rel_norm = rel_path.replace("\\", "/").lower()
        for p in preserve_set:
            if rel_norm == p or rel_norm.startswith(p + "/"):
                return True
        return False

    try:
        for src in extracted_root.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(extracted_root).as_posix()
            if _is_preserved(rel):
                logger.debug("Auto-update : preserve {}", rel)
                continue
            dst = project_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"Erreur copie : {exc}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _progress("Terminé", 1.0)

    # 5. Met à jour le fichier VERSION local (au cas où le zip n'en avait pas)
    try:
        (project_root / "VERSION").write_text(info.latest_version + "\n", encoding="utf-8")
    except Exception:
        pass

    return True, f"Mis à jour vers v{info.latest_version}. Redémarre le bot."


def set_github_repo(repo: str) -> None:
    """Permet de configurer le repo GitHub à l'exécution (pour les tests)."""
    global GITHUB_REPO
    GITHUB_REPO = repo
