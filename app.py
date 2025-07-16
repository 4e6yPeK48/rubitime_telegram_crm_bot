from flask import Flask, request, send_from_directory, redirect, url_for, render_template_string, session, flash, \
    get_flashed_messages
import asyncio
from static.models import Cooperator, Service, async_session, init_db

app = Flask(__name__, static_folder="static")
app.secret_key = "supersecretkey"  # для сессий и flash

LOGIN = "root"
PASSWORD = "root"

LOGIN_FORM = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Вход</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.7/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-LN+7fdVzj6u52u30Kp6M/trliBMCMKTyK833zpbD+pXdCLuTusPj697FH4R/5mcr" crossorigin="anonymous">
  </head>
  <body>
    <div class="container py-5">
      <div class="row justify-content-center">
        <div class="col-md-4">
          <div class="card shadow">
            <div class="card-body">
              <h2 class="card-title mb-4 text-center">Вход</h2>
              {% with messages = get_flashed_messages() %}
                {% if messages %}
                  <div class="alert alert-danger" role="alert">{{ messages[0] }}</div>
                {% endif %}
              {% endwith %}
              <form method="post" action="/login">
                <div class="mb-3">
                  <input type="text" name="login" class="form-control" placeholder="Логин" required>
                </div>
                <div class="mb-3">
                  <input type="password" name="password" class="form-control" placeholder="Пароль" required>
                </div>
                <button type="submit" class="btn btn-primary w-100">Войти</button>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.7/dist/js/bootstrap.bundle.min.js" integrity="sha384-ndDqU0Gzau9qJ1lfW4pNLlhNTkCfHzAVBReH9diLvGRem5+R9g2FzA8ZGN954O5Q" crossorigin="anonymous"></script>
  </body>
</html>
"""

def is_logged_in():
    return session.get("logged_in", False)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login = request.form.get("login", "")
        password = request.form.get("password", "")
        if login == LOGIN and password == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        else:
            flash("Неверный логин или пароль")
            return render_template_string(LOGIN_FORM)
    return render_template_string(LOGIN_FORM)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.before_request
def require_login():
    if request.endpoint in ("login", "static_files"):
        return
    if not is_logged_in():
        return redirect(url_for("login"))

@app.route("/")
def index():
    # Получаем сообщения с категориями
    messages = get_flashed_messages(with_categories=True)
    return render_template_string(
        open("static/main.html", encoding="utf-8").read(),
        messages=messages
    )

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route("/add_cooperator", methods=["POST"])
def add_cooperator():
    data = request.form
    try:
        cooperator_id = int(data.get("id", ""))
        branch_id = int(data.get("branch_id", ""))
        name = data.get("name", "").strip()
        if not name:
            flash("Имя сотрудника не может быть пустым", "error")
            return redirect(url_for("index"))
    except Exception:
        flash("Некорректные данные сотрудника", "error")
        return redirect(url_for("index"))

    async def add():
        async with async_session() as session:
            exists = await session.get(Cooperator, cooperator_id)
            if exists:
                return False
            cooperator = Cooperator(
                id=cooperator_id,
                branch_id=branch_id,
                name=name
            )
            session.add(cooperator)
            await session.commit()
            return True

    result = asyncio.run(add())
    if not result:
        flash("Сотрудник с таким ID уже существует", "error")
        return redirect(url_for("index"))
    flash("Сотрудник успешно добавлен!", "success")
    return redirect(url_for("index"))

@app.route("/add_service", methods=["POST"])
def add_service():
    data = request.form
    try:
        service_id = int(data.get("id", ""))
        branch_id = int(data.get("branch_id", ""))
        cooperator_id = int(data.get("cooperator_id", ""))
        name = data.get("name", "").strip()
        price = float(data.get("price", ""))
        duration = int(data.get("duration", ""))
        if not name:
            flash("Название услуги не может быть пустым", "error")
            return redirect(url_for("index"))
    except Exception:
        flash("Некорректные данные услуги", "error")
        return redirect(url_for("index"))

    async def add():
        async with async_session() as session:
            exists = await session.get(Service, service_id)
            if exists:
                return False
            service = Service(
                id=service_id,
                branch_id=branch_id,
                cooperator_id=cooperator_id,
                name=name,
                price=price,
                duration=duration
            )
            session.add(service)
            await session.commit()
            return True

    result = asyncio.run(add())
    if not result:
        flash("Услуга с таким ID уже существует", "error")
        return redirect(url_for("index"))
    flash("Услуга успешно добавлена!", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        asyncio.run(init_db())
    app.run(debug=True)
