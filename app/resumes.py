"""简历库路由：每用户最多保存 3 份简历，可新建 / 覆盖 / 删除，优化时直接调用。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import SavedResume
from .rate_limit import enforce_rate_limit
from .schemas import ResumeCreate, ResumeSchema, ResumeUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resumes", tags=["resumes"])

MAX_RESUMES = 3


@router.get("", response_model=list[ResumeSchema])
def list_resumes(
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> list[ResumeSchema]:
    """列出当前用户的简历（按更新时间倒序，最多 3 份）。"""
    rows = s.scalars(
        select(SavedResume)
        .where(SavedResume.user_id == user.id)
        .order_by(SavedResume.updated_at.desc())
    ).all()
    return [_to_schema(r) for r in rows]


@router.post("", response_model=ResumeSchema, status_code=status.HTTP_201_CREATED)
def create_resume(
    req: ResumeCreate,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> ResumeSchema:
    """新建一份简历。每用户最多 3 份，超出返回 409 提示先覆盖/删除。"""
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="简历内容不能为空")
    count = int(
        s.scalar(
            select(func.count()).select_from(SavedResume).where(SavedResume.user_id == user.id)
        )
        or 0
    )
    if count >= MAX_RESUMES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"最多只能保存 {MAX_RESUMES} 份简历，请先删除或覆盖其中一份",
        )
    row = SavedResume(
        user_id=user.id,
        name=(req.name or "").strip()[:64] or "未命名简历",
        content=content,
        sections=req.sections,
        position_category=req.position_category,
        experience_level=req.experience_level,
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    logger.info("[resumes] 用户 #%s 保存简历：%s", user.id, row.name)
    return _to_schema(row)


@router.put("/{resume_id}", response_model=ResumeSchema)
def update_resume(
    resume_id: int,
    req: ResumeUpdate,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> ResumeSchema:
    """覆盖更新一份简历（仅本人）。"""
    row = s.scalar(
        select(SavedResume).where(
            SavedResume.id == resume_id, SavedResume.user_id == user.id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="简历不存在或无权访问")
    if req.name is not None:
        row.name = (req.name.strip() or "未命名简历")[:64]
    if req.content is not None:
        content = req.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="简历内容不能为空")
        row.content = content
    if req.sections is not None:
        row.sections = req.sections
    if req.position_category is not None:
        row.position_category = req.position_category
    if req.experience_level is not None:
        row.experience_level = req.experience_level
    s.commit()
    s.refresh(row)
    logger.info("[resumes] 用户 #%s 覆盖简历：%s", user.id, row.name)
    return _to_schema(row)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: int,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> None:
    """删除一份简历（仅本人）。"""
    row = s.scalar(
        select(SavedResume).where(
            SavedResume.id == resume_id, SavedResume.user_id == user.id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="简历不存在或无权访问")
    s.delete(row)
    s.commit()
    logger.info("[resumes] 用户 #%s 删除简历 #%s", user.id, resume_id)


def _to_schema(row: SavedResume) -> ResumeSchema:
    return ResumeSchema(
        id=row.id,
        name=row.name,
        content=row.content,
        sections=row.sections,
        position_category=row.position_category,
        experience_level=row.experience_level,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )