from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User
from app.security import COOKIE_NAME, create_access_token
from app.services import generate_unique_username, new_connect_token

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = f"{settings.BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/?error=auth")

    info = token.get("userinfo") or await oauth.google.userinfo(token=token)
    google_id, email = info.get("sub"), info.get("email")
    if not google_id or not email or not info.get("email_verified", True):
        return RedirectResponse("/?error=email")

    user = (await db.execute(select(User).where(User.google_id == google_id))).scalar_one_or_none()
    if user is None:
        user = User(
            google_id=google_id,
            email=email,
            name=info.get("name") or email.split("@")[0],
            avatar_url=info.get("picture"),
            username=await generate_unique_username(db, email),
            connect_token=new_connect_token(),  # Telegramga ulash uchun maxfiy token
        )
        db.add(user)
    else:
        user.name = info.get("name") or user.name
        user.avatar_url = info.get("picture") or user.avatar_url
    await db.commit()
    await db.refresh(user)

    resp = RedirectResponse("/")
    resp.set_cookie(
        COOKIE_NAME,
        create_access_token(user.id),
        max_age=settings.JWT_EXPIRE_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/")
    resp.delete_cookie(COOKIE_NAME)
    return resp
