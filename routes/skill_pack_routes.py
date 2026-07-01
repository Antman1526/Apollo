"""Admin routes to install Agent Skills packs from GitHub / zip.

`POST /api/skills/packs/preview` fetches a pack (SSRF-guarded) and discovers its
skills without writing anything. `POST /api/skills/packs/install` installs a
confirmed selection into the SKILL.md store with provenance. Both are
admin-gated; the pure discover/install logic lives in
`services.skills.pack_installer`.
"""
import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from services.skills import pack_installer as pi


class PreviewRequest(BaseModel):
    source: str
    ref: str = ""


class InstallRequest(BaseModel):
    source: str
    ref: str = ""
    category: str = "imported"
    names: list = []          # skill names to install; empty = all discovered
    overwrite: bool = False


def setup_skill_pack_routes(skills_manager) -> APIRouter:
    router = APIRouter(prefix="/api/skills/packs", tags=["skills"])

    def _skills_root() -> str:
        # SkillsManager stores SKILL.md files under `<data_dir>/skills/` and
        # exposes that directly as `skills_root`.
        return skills_manager.skills_root

    @router.post("/preview")
    async def preview(request: Request, body: PreviewRequest):
        require_admin(request)
        root = pi.fetch_pack(body.source, body.ref)
        found = pi.discover_skills(root)
        return {"ok": True, "root": root, "skills": [
            {"name": f.name, "description": f.description, "tier": f.tier,
             "rel_dir": f.rel_dir, "error": f.error} for f in found]}

    @router.post("/install")
    async def install(request: Request, body: InstallRequest):
        require_admin(request)
        root = pi.fetch_pack(body.source, body.ref)
        found = pi.discover_skills(root)
        if body.names:
            found = [f for f in found if f.name in set(body.names)]
        # Timezone-aware UTC (utcnow() is deprecated).
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        opts = pi.InstallOpts(
            category=body.category,      # sanitized (slugified) inside install_skills
            owner=get_current_user(request) or None,
            source_url=body.source,
            source_ref=body.ref or "HEAD",
            now_iso=now_iso,
            overwrite=body.overwrite,
        )
        res = pi.install_skills(found, opts, _skills_root(), src_root=root)
        return {"ok": True, **res}

    return router
