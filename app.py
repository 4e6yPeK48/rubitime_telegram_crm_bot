import datetime
import os
import traceback
from collections import defaultdict
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, Form, status, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from pydantic import BaseModel, ValidationError, constr, conint, confloat
from sqlalchemy import select

from main import log_func_call
from services.auth_service import create_access_token
from services.cooperator_service import get_cooperators, add_cooperator
from services.service_service import get_services, add_service
from static.models import init_db, ReminderRecord, async_session

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
WEB_LOGIN = os.getenv("WEB_LOGIN")
WEB_PASSWORD = os.getenv("WEB_PASSWORD")

LOGIN_ATTEMPTS_LIMIT = int(os.getenv("LOGIN_ATTEMPTS_LIMIT"))
LOGIN_ATTEMPTS_WINDOW = int(os.getenv("LOGIN_ATTEMPTS_WINDOW"))
login_attempts = defaultdict(list)


@asynccontextmanager
async def lifespan(app):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="static/html")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class CooperatorForm(BaseModel):
    id: conint(gt=0)
    branch_id: conint(gt=0)
    name: constr(min_length=1, max_length=100)


class ServiceForm(BaseModel):
    id: conint(gt=0)
    branch_id: conint(gt=0)
    cooperator_id: conint(gt=0)
    name: constr(min_length=1, max_length=100)
    price: confloat(gt=0)
    duration: conint(gt=0, le=480)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="Could not validate credentials",
        headers={"Location": "/login"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        login = payload.get("login")
        if login != WEB_LOGIN:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return login


def get_token_from_cookie(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return token


def is_login_allowed(ip: str) -> bool:
    now = datetime.datetime.now().timestamp()
    attempts = login_attempts[ip]
    login_attempts[ip] = [ts for ts in attempts if now - ts < LOGIN_ATTEMPTS_WINDOW]
    return len(login_attempts[ip]) < LOGIN_ATTEMPTS_LIMIT


def register_login_attempt(ip: str):
    now = datetime.datetime.now().timestamp()
    login_attempts[ip].append(now)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    msg = request.query_params.get("msg")
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg})


@app.post("/login")
async def login_post(request: Request, login: str = Form(...), password: str = Form(...)):
    ip = request.client.host
    if not is_login_allowed(ip):
        return RedirectResponse(url="/login?msg=Слишком+много+попыток,+попробуйте+через+10+минут", status_code=303)
    register_login_attempt(ip)
    if login == WEB_LOGIN and password == WEB_PASSWORD:
        token = create_access_token({"login": login})
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("access_token", token, httponly=True, max_age=8 * 3600)
        login_attempts[ip] = []
        return response
    return RedirectResponse(url="/login?msg=Неверный+логин+или+пароль", status_code=303)


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, token: str = Depends(get_token_from_cookie)):
    msg = request.query_params.get("msg")
    cooperators = await get_cooperators()
    services = await get_services()
    cooperator_id_names = [f"{c.id} | {c.name}" for c in cooperators]
    service_id_names = [f"{s.id} | {s.name}" for s in services]
    return templates.TemplateResponse(
        "main.html",
        {
            "request": request,
            "messages": [("success", msg)] if msg else [],
            "cooperators": cooperators,
            "services": services,
            "cooperator_id_names": cooperator_id_names,
            "service_id_names": service_id_names
        }
    )


@app.get("/api/cooperators")
async def api_cooperators(token: str = Depends(get_token_from_cookie)):
    cooperators = await get_cooperators()
    return [{"id": c.id, "branch_id": c.branch_id, "name": c.name} for c in cooperators]


@app.get("/api/services")
async def api_services(token: str = Depends(get_token_from_cookie)):
    services = await get_services()
    return [
        {
            "id": s.id,
            "branch_id": s.branch_id,
            "cooperator_id": s.cooperator_id,
            "name": s.name,
            "price": s.price,
            "duration": s.duration
        }
        for s in services
    ]


@app.post("/add_cooperator")
async def add_cooperator_route(
        request: Request,
        token: str = Depends(get_token_from_cookie),
        id: int = Form(...),
        branch_id: int = Form(...),
        name: str = Form(...)
):
    try:
        form = CooperatorForm(id=id, branch_id=branch_id, name=name)
    except ValidationError:
        return RedirectResponse(url="/?msg=Некорректные+данные+сотрудника", status_code=303)
    success = await add_cooperator(form.id, form.branch_id, form.name)
    if not success:
        return RedirectResponse(url="/?msg=Сотрудник+с+таким+ID+уже+существует", status_code=303)
    return RedirectResponse(url="/?msg=Сотрудник+успешно+добавлен!", status_code=303)


@app.post("/add_service")
async def add_service_route(
        request: Request,
        token: str = Depends(get_token_from_cookie),
        id: int = Form(...),
        branch_id: int = Form(...),
        cooperator_id: int = Form(...),
        name: str = Form(...),
        price: float = Form(...),
        duration: int = Form(...)
):
    try:
        form = ServiceForm(
            id=id, branch_id=branch_id, cooperator_id=cooperator_id,
            name=name, price=price, duration=duration
        )
    except ValidationError:
        return RedirectResponse(url="/?msg=Некорректные+данные+услуги", status_code=303)
    success = await add_service(
        form.id, form.branch_id, form.cooperator_id,
        form.name, form.price, form.duration
    )
    if not success:
        return RedirectResponse(url="/?msg=Услуга+с+таким+ID+уже+существует", status_code=303)
    return RedirectResponse(url="/?msg=Услуга+успешно+добавлена!", status_code=303)


@app.post("/webhook")
async def webhook(request: Request):
    log_func_call("webhook", f"request from {request.client.host}")
    try:
        data = await request.json()
        log_func_call("webhook", f"event={data.get('event')}, data={data.get('data')}")
        event = data.get("event")
        record_data = data.get("data", {})
        rubitime_id = record_data.get("id")
        async with async_session() as session:
            rec = await session.execute(
                select(ReminderRecord).where(ReminderRecord.rubitime_id == rubitime_id)
            )
            rec = rec.scalars().first()
            dt_str = record_data.get("record")
            name = record_data.get("name", "")
            phone = record_data.get("phone", "")
            user_id = record_data.get("user_id", None)
            dt = None
            try:
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            if event == "event-create-record":
                log_func_call("webhook", f"event-create-record rubitime_id={rubitime_id}")
                if not rec and dt and user_id:
                    new_rec = ReminderRecord(
                        rubitime_id=rubitime_id,
                        user_id=user_id,
                        datetime=dt,
                        name=name,
                        phone=phone
                    )
                    session.add(new_rec)
                    await session.commit()
            elif event == "event-update-record":
                log_func_call("webhook", f"event-update-record rubitime_id={rubitime_id}")
                if rec and dt:
                    rec.datetime = dt
                    rec.name = name
                    rec.phone = phone
                    await session.commit()
            elif event == "event-remove-record":
                log_func_call("webhook", f"event-remove-record rubitime_id={rubitime_id}")
                if rec:
                    await session.delete(rec)
                    await session.commit()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        log_func_call("webhook", f"error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/token")
async def login_token(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == WEB_LOGIN and form_data.password == WEB_PASSWORD:
        token = create_access_token({"login": form_data.username})
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=400, detail="Incorrect username or password")


@app.get("/me")
async def me(token: str = Depends(get_token_from_cookie)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        login = payload.get("login")
        return {"login": login}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
