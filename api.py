"""HTTP API for CAKTO ADS AI. Static UI and JSON API are served same-origin."""
from __future__ import annotations

import json
import os
import re
import traceback
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from auth import create_reset, create_session, consume_reset, hash_password, is_guest_email, new_id, token_digest, user_for_session, verify_password
from database import INTEGRITY_ERRORS, db_session, json_dump, json_load, utc_now
from services import analytics, automation, campaign_agent, creative_pack, exporter, generator, improvement, learning, policy, strategy, url_analyzer

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


PRODUCT_TEXT_FIELDS = (
    "name", "description", "niche", "category", "sales_url", "checkout_url",
    "benefits", "differentiators", "target_audience", "guarantee", "bonuses", "notes",
    "headline", "subheadline", "faq", "testimonials", "cta", "mechanism", "relevant_links",
)
ITEM_TABLES = {"avatars": "avatars", "angles": "angles", "offers": "offers", "copies": "copies", "creatives": "creatives"}
SELECTIONS = {
    "avatar": ("selected_avatar_id", "avatars", "angles"),
    "angle": ("selected_angle_id", "angles", "offers"),
    "offer": ("selected_offer_id", "offers", "copies"),
    "copy": ("selected_copy_id", "copies", "creatives"),
    "creative": ("selected_creative_id", "creatives", "campaign"),
}
STAGE_INDEX = {"product": 0, "avatar": 1, "angle": 2, "offer": 3, "copy": 4, "creative": 5, "campaign": 6, "metrics": 7}


def clean_text(value, name, maximum=10000, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise HTTPError(400, f"{name} é obrigatório.")
    if len(text) > maximum:
        raise HTTPError(400, f"{name} excede o limite de {maximum} caracteres.")
    return text


def price_value(value, field):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPError(400, f"{field} deve ser um número.")
    if number < 0 or number > 100000000:
        raise HTTPError(400, f"{field} está fora do intervalo permitido.")
    return round(number, 2)


def product_payload(body, existing=None):
    previous = existing or {}
    result = {}
    for field in PRODUCT_TEXT_FIELDS:
        if field in body:
            result[field] = clean_text(body[field], field.replace("_", " ").capitalize(), 10000, field == "name")
        elif existing:
            result[field] = previous.get(field, "")
        else:
            result[field] = ""
    for field in ("price", "promo_price", "unit_cost"):
        if field in body:
            result[field] = price_value(body[field], field)
        elif existing:
            result[field] = previous.get(field)
        else:
            result[field] = None
    return result


def email_valid(email):
    return len(email) <= 254 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))


def parse_json_column(row, field="data", fallback=None):
    return json_load(row[field], fallback if fallback is not None else {}) if row else fallback


def store_page_analysis(conn, user_id, product_id, analysis):
    if not isinstance(analysis, dict) or not analysis.get("source_url"):
        return
    now = utc_now()
    conn.execute(
        "INSERT INTO product_analyses(id,user_id,product_id,source_url,status,data,created_at) VALUES(?,?,?,?,?,?,?)",
        (new_id(), user_id, product_id, str(analysis.get("source_url", ""))[:2000], str(analysis.get("status", "unknown"))[:40], json_dump(analysis), now),
    )


def item_public(row):
    data = json_load(row["data"], {})
    data.update({"id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
    return data


def project_public(conn, project_row, full=True):
    if not project_row:
        return None
    project = dict(project_row)
    product = conn.execute("SELECT * FROM products WHERE id=? AND user_id=?", (project["product_id"], project["user_id"])).fetchone()
    project["product"] = dict(product) if product else None
    insight_row = conn.execute("SELECT data,updated_at FROM project_insights WHERE project_id=? AND user_id=?", (project["id"], project["user_id"])).fetchone()
    project["xray"] = json_load(insight_row["data"], {}) if insight_row else None
    project["xray_updated_at"] = insight_row["updated_at"] if insight_row else None
    analysis_row = conn.execute("SELECT status,data,created_at FROM product_analyses WHERE product_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1", (project["product_id"], project["user_id"])).fetchone()
    project["source_analysis"] = (json_load(analysis_row["data"], {}) | {"status": analysis_row["status"], "created_at": analysis_row["created_at"]}) if analysis_row else None
    stage_has_data = [bool(project.get("product_id"))]
    for stage_table in ("avatars", "angles", "offers", "copies", "creatives", "campaigns", "metrics"):
        exists = conn.execute(f"SELECT 1 FROM {stage_table} WHERE project_id=? AND user_id=? LIMIT 1", (project["id"], project["user_id"])).fetchone()
        stage_has_data.append(bool(exists))
    project["progress_count"] = sum(stage_has_data)
    if full:
        for kind, table in ITEM_TABLES.items():
            rows = conn.execute(f"SELECT * FROM {table} WHERE project_id=? AND user_id=? ORDER BY created_at ASC,id ASC", (project["id"], project["user_id"])).fetchall()
            project[kind] = [item_public(row) for row in rows]
        campaign_row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project["id"], project["user_id"])).fetchone()
        if campaign_row:
            project["campaign"] = json_load(campaign_row["data"], {}) | {"id": campaign_row["id"], "updated_at": campaign_row["updated_at"]}
        else:
            project["campaign"] = None
        metric_rows = conn.execute("SELECT * FROM metrics WHERE project_id=? AND user_id=? ORDER BY created_at DESC,id DESC", (project["id"], project["user_id"])).fetchall()
        project["metrics"] = [json_load(row["data"], {}) | {"id": row["id"], "created_at": row["created_at"]} for row in metric_rows]
        test_rows = conn.execute("SELECT * FROM ab_tests WHERE project_id=? AND user_id=? ORDER BY created_at DESC,id DESC", (project["id"], project["user_id"])).fetchall()
        project["tests"] = [json_load(row["data"], {}) | {"id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in test_rows]
    return project


def require_project(conn, project_id, user_id):
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not row:
        raise HTTPError(404, "Projeto não encontrado.")
    return row


def selected_data(project, field, collection):
    item_id = project.get(field)
    return next((item for item in project.get(collection, []) if item.get("id") == item_id), None)


def insert_item(conn, table, user_id, project_id, data, item_id=None):
    now = utc_now()
    item_id = item_id or new_id()
    data = dict(data)
    data["id"] = item_id
    conn.execute(
        f"INSERT INTO {table}(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (item_id, user_id, project_id, json_dump(data), now, now),
    )
    return item_id


def mark_stale(conn, project_id, stale_from):
    conn.execute("UPDATE projects SET stale_from=?,updated_at=? WHERE id=?", (stale_from, utc_now(), project_id))


def deep_remap(value, mapping):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.endswith("_id") and isinstance(item, str) and item in mapping:
                result[key] = mapping[item]
            else:
                result[key] = deep_remap(item, mapping)
        return result
    if isinstance(value, list):
        return [deep_remap(item, mapping) for item in value]
    return value


def campaign_policy_review(conn, user_id, project_id, project, campaign_data):
    ads = campaign_data.get("ads") or []
    if not ads:
        ads = [{"ad_name": "Copy selecionada", "copy_id": project.get("selected_copy_id")}]
    ad_reviews = []
    all_findings = []
    risks = []
    first_suggestion = ""
    for ad in ads:
        copy_id = ad.get("copy_id")
        copy_row = conn.execute("SELECT data FROM copies WHERE id=? AND project_id=? AND user_id=?", (copy_id, project_id, user_id)).fetchone() if copy_id else None
        copy_data = json_load(copy_row["data"], {}) if copy_row else {}
        review_text = "\n".join(part for part in (
            copy_data.get("text") or ad.get("text") or "", ad.get("hook", ""), ad.get("headline", ""),
            ad.get("description", ""), ad.get("cta", ""),
        ) if str(part or "").strip())
        review = policy.check_copy(review_text)
        risks.append(review.get("risk", "Sem análise"))
        if review.get("suggested_version") and not first_suggestion:
            first_suggestion = review["suggested_version"]
        ad_reviews.append({"ad_name": ad.get("ad_name", "Anúncio"), "copy_id": copy_id, "risk": review.get("risk"), "findings": review.get("findings", []), "suggested_version": review.get("suggested_version")})
        for finding in review.get("findings", []):
            all_findings.append({**finding, "ad_name": ad.get("ad_name", "Anúncio")})
    overall = "Alto" if "Alto" in risks else "Médio" if "Médio" in risks else "Baixo" if risks else "Sem análise"
    return {
        "risk": overall, "ad_count": len(ad_reviews), "ad_reviews": ad_reviews,
        "findings": all_findings,
        "suggested_version": first_suggestion or "Nenhum ajuste automático foi sugerido.",
        "note": "Revisão heurística local. Não é aprovação do Meta e não garante conformidade.",
        "checked_at": utc_now(),
    }


def campaign_agent_run_public(conn, row, user_id, include_project=True):
    if not row:
        return None
    data = json_load(row["data"], {}) or {}
    result = {
        "id": row["id"], "user_id": row["user_id"], "product_id": row["product_id"],
        "project_id": row["project_id"], "title": row["title"], "status": row["status"],
        "selected_strategy_id": row["selected_strategy_id"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], **data,
    }
    if include_project and row["project_id"]:
        project_row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (row["project_id"], user_id)).fetchone()
        result["project"] = project_public(conn, project_row) if project_row else None
    else:
        result["project"] = None
    return result


def campaign_agent_metric_summary(conn, user_id, project_id):
    if not project_id:
        return {"records": 0, "impressions": 0, "clicks": 0, "purchases": 0, "spend": 0.0, "revenue": 0.0, "ctr": None, "cpa": None, "roas": None}
    rows = conn.execute("SELECT data FROM metrics WHERE project_id=? AND user_id=? ORDER BY created_at DESC,id DESC", (project_id, user_id)).fetchall()
    records = [json_load(row["data"], {}) for row in rows]
    impressions = sum(int(item.get("impressions") or 0) for item in records)
    clicks = sum(int(item.get("clicks") or 0) for item in records)
    purchases = sum(int(item.get("purchases") or 0) for item in records)
    spend = sum(float(item.get("spend") or 0) for item in records)
    revenue = sum(float(item.get("revenue") or 0) for item in records)
    return {
        "records": len(records), "impressions": impressions, "clicks": clicks, "purchases": purchases,
        "spend": round(spend, 2), "revenue": round(revenue, 2),
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpa": round(spend / purchases, 2) if purchases else None,
        "roas": round(revenue / spend, 2) if spend else None,
        "sample_note": "Agregado descritivo; não declara causalidade ou vencedor estatístico.",
    }


def campaign_agent_history(conn, user_id):
    rows = conn.execute(
        "SELECT r.*,p.name AS product_name,pr.name AS project_name FROM campaign_agent_runs r "
        "LEFT JOIN products p ON p.id=r.product_id AND p.user_id=r.user_id "
        "LEFT JOIN projects pr ON pr.id=r.project_id AND pr.user_id=r.user_id "
        "WHERE r.user_id=? ORDER BY r.updated_at DESC,r.created_at DESC", (user_id,),
    ).fetchall()
    result = []
    groups = {}
    for row in rows:
        data = json_load(row["data"], {}) or {}
        selected = data.get("selected_strategy") or next((item for item in data.get("strategies", []) if item.get("id") == row["selected_strategy_id"]), None)
        metric = campaign_agent_metric_summary(conn, user_id, row["project_id"])
        item = {
            "id": row["id"], "title": row["title"], "status": row["status"],
            "product_id": row["product_id"], "product_name": row["product_name"] or (data.get("brief", {}).get("product", {}).get("name") if data.get("brief") else "Produto removido"),
            "project_id": row["project_id"], "project_name": row["project_name"],
            "selected_strategy_id": row["selected_strategy_id"], "selected_strategy_name": selected.get("name") if selected else "Aguardando escolha",
            "recommendation_id": data.get("recommendation_id"), "recommendation_reason": data.get("recommendation_reason"),
            "created_at": row["created_at"], "updated_at": row["updated_at"], "metrics": metric,
        }
        result.append(item)
        strategy_key = (row["product_id"] or row["id"], row["selected_strategy_id"])
        if row["selected_strategy_id"]:
            group = groups.setdefault(strategy_key, {
                "product_id": row["product_id"], "product_name": item["product_name"],
                "strategy_id": row["selected_strategy_id"], "strategy_name": item["selected_strategy_name"],
                "runs": 0, "metric_records": 0, "impressions": 0, "clicks": 0, "purchases": 0, "spend": 0.0, "revenue": 0.0,
            })
            group["runs"] += 1
            for field in ("metric_records", "impressions", "clicks", "purchases"):
                source = "records" if field == "metric_records" else field
                group[field] += int(metric.get(source) or 0)
            group["spend"] += float(metric.get("spend") or 0)
            group["revenue"] += float(metric.get("revenue") or 0)
    comparisons = []
    for group in groups.values():
        group["spend"] = round(group["spend"], 2)
        group["revenue"] = round(group["revenue"], 2)
        group["ctr"] = round(group["clicks"] / group["impressions"] * 100, 2) if group["impressions"] else None
        group["cpa"] = round(group["spend"] / group["purchases"], 2) if group["purchases"] else None
        group["roas"] = round(group["revenue"] / group["spend"], 2) if group["spend"] else None
        group["comparison_status"] = "Descritivo; amostra e atribuição não avaliadas estatisticamente. Não declarar vencedor."
        comparisons.append(group)
    return result, comparisons


class APIHandler(BaseHTTPRequestHandler):
    server_version = "CaktoAdsAI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Keep preview logs concise and avoid logging credentials or query data.
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _send_json(self, payload, status=200, headers=None):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if headers:
            for key, value in headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, payload, status=200, headers=None):
        raw = bytes(payload)
        headers = headers or []
        content_type = next((value for key, value in headers if key.lower() == "content-type"), "application/octet-stream")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers:
            if key.lower() != "content-type":
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path):
        if not path.is_file():
            raise HTTPError(404, "Arquivo não encontrado.")
        content_type = {
            ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        }.get(path.suffix.lower(), "application/octet-stream")
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if path.suffix.lower() == ".html":
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise HTTPError(400, "Corpo da requisição inválido.")
        if length > 2_000_000:
            raise HTTPError(413, "A requisição excede o limite permitido.")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPError(400, "Envie um JSON válido.")
        if not isinstance(body, dict):
            raise HTTPError(400, "O corpo deve ser um objeto JSON.")
        return body

    def _cookie_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        return cookie.get("cakto_session").value if cookie.get("cakto_session") else ""

    def _bearer_token(self):
        authorization = self.headers.get("Authorization", "").strip()
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            return token.strip()
        return ""

    def _current_user(self, conn):
        cookie_token = self._cookie_token()
        user = user_for_session(conn, cookie_token) if cookie_token else None
        if user:
            return user

        # Embedded previews may block or partition cookies inconsistently.
        # Bearer fallback is deliberately limited to disposable guest accounts;
        # private accounts remain authenticated only by their HttpOnly cookie.
        bearer_token = self._bearer_token()
        if bearer_token:
            user = user_for_session(conn, bearer_token)
            if user and is_guest_email(user["email"]):
                return user
        return None

    def _require_user(self, conn):
        user = self._current_user(conn)
        if not user:
            raise HTTPError(401, "Sua sessão expirou. Entre novamente.")
        return user

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise HTTPError(405, "Método não permitido.")
                if path == "/" or path == "/index.html":
                    return self._send_file(STATIC / "index.html")
                if path.startswith("/static/"):
                    relative = path[len("/static/"):]
                    candidate = (STATIC / relative).resolve()
                    if STATIC.resolve() not in candidate.parents:
                        raise HTTPError(404, "Arquivo não encontrado.")
                    return self._send_file(candidate)
                raise HTTPError(404, "Rota não encontrada.")
            body = self._body() if method in ("POST", "PUT", "PATCH") else {}
            with db_session() as conn:
                result, status, headers = self._api(conn, method, path, body)
            if isinstance(result, (bytes, bytearray)):
                self._send_bytes(result, status, headers)
            else:
                self._send_json(result, status, headers)
        except HTTPError as error:
            self._send_json({"error": error.message}, error.status)
        except INTEGRITY_ERRORS as error:
            message = "Não foi possível concluir a operação. Confira os vínculos e tente novamente."
            if "products.id" in str(error) or "FOREIGN KEY" in str(error):
                message = "Este produto está vinculado a projetos. Exclua ou altere os projetos antes de remover o produto."
            self._send_json({"error": message}, 409)
        except ValueError as error:
            self._send_json({"error": str(error)}, 400)
        except Exception as error:
            print("API error:", repr(error))
            traceback.print_exc()
            self._send_json({"error": "Erro interno. Tente novamente; seus dados salvos foram preservados."}, 500)

    def _api(self, conn, method, path, body):
        if path == "/api/health" and method == "GET":
            return {"ok": True, "service": "JOKERS IA CAMPAIGN AGENT", "workspace": "CAKTO ADS AI", "version": "1.0.0"}, 200, []

        # Public authentication routes.
        if path == "/api/auth/register" and method == "POST":
            name = clean_text(body.get("full_name"), "Nome", 120, True)
            email = clean_text(body.get("email"), "E-mail", 254, True).lower()
            password = str(body.get("password") or "")
            if not email_valid(email):
                raise HTTPError(400, "Informe um e-mail válido.")
            if len(password) < 8:
                raise HTTPError(400, "A senha precisa ter pelo menos 8 caracteres.")
            if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HTTPError(409, "Já existe uma conta com este e-mail.")
            user_id, now = new_id(), utc_now()
            conn.execute("INSERT INTO users(id,email,full_name,password_hash,created_at) VALUES(?,?,?,?,?)", (user_id, email, name, hash_password(password), now))
            raw = create_session(conn, user_id)
            user = {"id": user_id, "email": email, "full_name": name, "created_at": now, "is_guest": False}
            return {"user": user}, 201, self._session_cookie(raw)

        if path == "/api/auth/guest" and method == "POST":
            now = utc_now()
            guest_cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat(timespec="seconds")
            conn.execute("DELETE FROM users WHERE email LIKE ? AND created_at<?", ("guest-%@guest.invalid", guest_cutoff))
            user_id = new_id()
            email = f"guest-{user_id}@guest.invalid"
            # A random, undisclosed password hash satisfies the existing schema;
            # this guest can only use the session created in this request.
            conn.execute(
                "INSERT INTO users(id,email,full_name,password_hash,created_at) VALUES(?,?,?,?,?)",
                (user_id, email, "Visitante", hash_password(new_id() + new_id()), now),
            )
            raw = create_session(conn, user_id)
            user = {"id": user_id, "email": email, "full_name": "Visitante", "created_at": now, "is_guest": True}
            # The bearer fallback is guest-only and supports sandboxed/embedded previews
            # where browsers can decline to persist the HttpOnly session cookie.
            return {"user": user, "session_token": raw}, 201, self._session_cookie(raw)

        if path == "/api/auth/login" and method == "POST":
            email = clean_text(body.get("email"), "E-mail", 254, True).lower()
            password = str(body.get("password") or "")
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                raise HTTPError(401, "E-mail ou senha incorretos.")
            raw = create_session(conn, row["id"])
            user = {key: row[key] for key in ("id", "email", "full_name", "created_at")}
            user["is_guest"] = is_guest_email(user["email"])
            return {"user": user}, 200, self._session_cookie(raw)

        if path == "/api/auth/logout" and method == "POST":
            tokens = dict.fromkeys((self._cookie_token(), self._bearer_token()))
            for raw in tokens:
                if not raw:
                    continue
                token_hash = token_digest(raw)
                session = conn.execute("SELECT user_id FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
                guest = None
                if session:
                    guest = conn.execute("SELECT email FROM users WHERE id=?", (session["user_id"],)).fetchone()
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
                if guest and is_guest_email(guest["email"]):
                    # Guest workspaces are disposable; remove their data on explicit sign-out.
                    conn.execute("DELETE FROM users WHERE id=?", (session["user_id"],))
            return {"ok": True}, 200, self._clear_cookie()

        if path == "/api/auth/forgot-password" and method == "POST":
            email = clean_text(body.get("email"), "E-mail", 254, True).lower()
            row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            mode = os.environ.get("RESET_MODE", "disabled").lower()
            if mode != "local":
                return {"message": "A recuperação por e-mail não está configurada nesta implantação; nenhum e-mail foi enviado."}, 200, []
            if not row:
                # Keep the public response generic so account existence is not disclosed.
                return {"message": "Se o e-mail estiver cadastrado, as instruções estarão disponíveis para recuperação."}, 200, []
            token = create_reset(conn, row["id"])
            response = {"message": "Solicitação criada. Modo local: use o token temporário exibido abaixo."}
            response["reset_token"] = token
            return response, 200, []

        if path == "/api/auth/reset-password" and method == "POST":
            token = clean_text(body.get("token"), "Token", 500, True)
            password = str(body.get("new_password") or "")
            if len(password) < 8:
                raise HTTPError(400, "A nova senha precisa ter pelo menos 8 caracteres.")
            if not consume_reset(conn, token, hash_password(password)):
                raise HTTPError(400, "Token inválido, expirado ou já utilizado. Solicite uma nova redefinição.")
            return {"message": "Senha redefinida. Entre com a nova senha."}, 200, self._clear_cookie()

        user = self._require_user(conn)
        user_id = user["id"]

        def agent_context(payload):
            source = str(payload.get("source") or payload.get("product_source") or "manual").strip().lower()
            existing = None
            if source == "existing":
                product_id = clean_text(payload.get("product_id"), "Produto", 200, True)
                existing_row = conn.execute("SELECT * FROM products WHERE id=? AND user_id=?", (product_id, user_id)).fetchone()
                if not existing_row:
                    raise HTTPError(404, "Produto não encontrado nesta conta.")
                existing = dict(existing_row)
            page_analysis = None
            if source == "url":
                supplied_analysis = payload.get("page_analysis") if isinstance(payload.get("page_analysis"), dict) else {}
                analysis_id = clean_text(supplied_analysis.get("analysis_id"), "Análise de URL", 200, True)
                requested_url = clean_text(payload.get("url"), "URL", 2000, True)
                now_for_cleanup = utc_now()
                conn.execute("DELETE FROM campaign_agent_page_analyses WHERE expires_at<=?", (now_for_cleanup,))
                analysis_row = conn.execute("SELECT * FROM campaign_agent_page_analyses WHERE id=? AND user_id=?", (analysis_id, user_id)).fetchone()
                if not analysis_row or analysis_row["expires_at"] <= utc_now():
                    raise HTTPError(400, "A análise pública expirou ou não pertence a esta conta. Analise a URL novamente.")
                if requested_url != analysis_row["requested_url"]:
                    raise HTTPError(400, "A URL mudou após a análise. Analise novamente e revise a página atual.")
                page_analysis = json_load(analysis_row["data"], {})
                if not page_analysis.get("source_url"):
                    raise HTTPError(400, "A análise pública não contém uma página de origem válida. Analise a URL novamente.")
            normalized_payload = dict(payload)
            if source == "url":
                normalized_payload["page_analysis"] = page_analysis
            product, brief = campaign_agent.normalize_product(normalized_payload, existing)
            diagnosis = campaign_agent.diagnose(brief, product)
            return source, existing, product, brief, diagnosis, page_analysis

        if path == "/api/agent/brief" and method == "POST":
            source, existing, product, brief, diagnosis, page_analysis = agent_context(body)
            return {
                "brief": brief, "diagnosis": diagnosis,
                "product_preview": product,
                "agent_mode": "local_rules",
                "notice": "Interpretação local revisável. Nenhum LLM, pesquisa de concorrentes ou integração externa está configurado.",
            }, 200, []

        if path == "/api/agent/strategies" and method == "POST":
            source, existing, product, brief, diagnosis, page_analysis = agent_context(body)
            product_name = clean_text(product.get("name"), "Nome do produto", 200, True)
            if product_name == "Não identificado na página." or product_name == "Produto a confirmar":
                raise HTTPError(400, "Confirme o nome do produto no briefing antes de criar estratégias.")
            if source == "existing":
                product_id = existing["id"]
            else:
                values = product_payload(product)
                product_id, now = new_id(), utc_now()
                columns = list(values.keys())
                conn.execute(
                    f"INSERT INTO products(id,user_id,{','.join(columns)},created_at,updated_at) VALUES({','.join(['?']*(len(columns)+4))})",
                    [product_id, user_id, *[values[key] for key in columns], now, now],
                )
                if source == "url":
                    store_page_analysis(conn, user_id, product_id, page_analysis)
            product_row = conn.execute("SELECT * FROM products WHERE id=? AND user_id=?", (product_id, user_id)).fetchone()
            strategy_result = campaign_agent.generate_strategies(brief)
            project_id, now = new_id(), utc_now()
            title = f"JOKERS IA — {product_name} — {date.today().isoformat()}"[:160]
            conn.execute(
                "INSERT INTO projects(id,user_id,product_id,name,status,current_step,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (project_id, user_id, product_id, title, "draft", 0, now, now),
            )
            run_id = new_id()
            run_data = {
                "brief": brief, "diagnosis": diagnosis,
                "strategies": strategy_result["items"],
                "recommendation_id": strategy_result["recommended_id"],
                "recommendation_reason": strategy_result["recommendation_reason"],
                "comparison_note": strategy_result["comparison_note"],
                "status": "strategies", "messages": [], "feedback_events": [],
                "generated_package": None,
                "agent_mode": "local_rules",
                "workflow_mode": "manual" if body.get("workflow_mode") == "manual" else "autopilot",
                "generation_gate": "Nenhum ativo final foi gerado; escolha uma estratégia para continuar.",
                "product_snapshot": {key: product_row[key] for key in PRODUCT_TEXT_FIELDS if key in product_row.keys()} | {"price": product_row["price"], "promo_price": product_row["promo_price"], "unit_cost": product_row["unit_cost"]},
            }
            conn.execute(
                "INSERT INTO campaign_agent_runs(id,user_id,product_id,project_id,title,status,selected_strategy_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, user_id, product_id, project_id, title, "strategies", "", json_dump(run_data), now, now),
            )
            run_row = conn.execute("SELECT * FROM campaign_agent_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
            project_row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
            return {
                "run": campaign_agent_run_public(conn, run_row, user_id),
                "project": project_public(conn, project_row),
                "strategies": strategy_result["items"],
                "generation_notice": "As três estratégias foram criadas. Avatar, ângulo, oferta, copies, conceitos e Meta Plan só serão gerados após a escolha.",
            }, 201, []

        if path == "/api/agent/history" and method == "GET":
            runs, comparisons = campaign_agent_history(conn, user_id)
            return {"runs": runs, "comparisons": comparisons, "note": "Comparações descritivas por estratégia/produto; amostra insuficiente para declarar causalidade ou vencedor estatístico."}, 200, []

        if path == "/api/agent/dashboard" and method == "GET":
            runs, comparisons = campaign_agent_history(conn, user_id)
            return {
                "counts": {"runs": len(runs), "strategies_ready": sum(1 for item in runs if item["status"] in ("strategies", "ready")), "blueprints": sum(1 for item in runs if item["status"] == "ready"), "metric_records": sum(item["metrics"]["records"] for item in runs)},
                "recent_runs": runs[:5], "comparisons": comparisons[:8],
                "note": "Métricas adicionadas manualmente; sem conexão com conta de anúncios.",
            }, 200, []

        agent_run_match = re.fullmatch(r"/api/agent/runs/([^/]+)(?:/(choose|chat|improve))?", path)
        if agent_run_match:
            run_id, suffix = agent_run_match.group(1), agent_run_match.group(2) or ""
            run_row = conn.execute("SELECT * FROM campaign_agent_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
            if not run_row:
                raise HTTPError(404, "Sessão do Campaign Agent não encontrada nesta conta.")
            run_data = json_load(run_row["data"], {}) or {}
            if not suffix and method == "GET":
                return {"run": campaign_agent_run_public(conn, run_row, user_id)}, 200, []

            if suffix == "choose" and method == "POST":
                if run_row["status"] == "ready":
                    raise HTTPError(409, "Esta sessão já tem blueprint. Crie uma nova estratégia para testar outra direção e preservar o histórico.")
                project_row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (run_row["project_id"], user_id)).fetchone() if run_row["project_id"] else None
                if not project_row:
                    raise HTTPError(409, "O projeto associado não está disponível.")
                prior_count = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND user_id=?", (project_row["id"], user_id)).fetchone()[0] for table in ITEM_TABLES.values())
                existing_campaign = conn.execute("SELECT 1 FROM campaigns WHERE project_id=? AND user_id=?", (project_row["id"], user_id)).fetchone()
                if prior_count or existing_campaign:
                    raise HTTPError(409, "O projeto já contém ativos. Nenhum dado será substituído; crie outra sessão para preservar as edições.")
                ids = body.get("strategy_ids")
                if not isinstance(ids, list):
                    ids = [body.get("strategy_id")]
                ids = [str(value) for value in ids if value]
                if len(ids) == 1:
                    selected_strategy = next((item for item in run_data.get("strategies", []) if item.get("id") == ids[0]), None)
                    if not selected_strategy:
                        raise HTTPError(400, "Selecione uma das três estratégias desta sessão.")
                elif len(ids) == 2:
                    try:
                        selected_strategy = campaign_agent.combine_strategies(run_data.get("strategies", []), ids)
                    except ValueError as error:
                        raise HTTPError(400, str(error))
                else:
                    raise HTTPError(400, "Escolha uma estratégia ou combine exatamente duas.")
                project = project_public(conn, project_row)
                product = project.get("product") or {}
                package = campaign_agent.build_selected_package(run_data.get("brief", {}), selected_strategy, product)
                avatar, angle = package["avatar"], package["angle"]
                insert_item(conn, "avatars", user_id, project_row["id"], avatar, avatar["id"])
                insert_item(conn, "angles", user_id, project_row["id"], angle, angle["id"])
                for offer in package["offers"]:
                    insert_item(conn, "offers", user_id, project_row["id"], offer, offer["id"])
                for copy_item in package["copies"]:
                    insert_item(conn, "copies", user_id, project_row["id"], copy_item, copy_item["id"])
                for creative in package["creatives"]:
                    insert_item(conn, "creatives", user_id, project_row["id"], creative, creative["id"])
                for test in package["test_plan"]:
                    now_test = utc_now()
                    conn.execute("INSERT INTO ab_tests(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (test["id"], user_id, project_row["id"], json_dump(test), now_test, now_test))
                campaign_id, now = new_id(), utc_now()
                selected_copy = next(item for item in package["copies"] if item["type"] == "primary")
                selected_image = next(item for item in package["creatives"] if item.get("media_type") == "image")
                product_brief = run_data.get("brief", {}).get("product", {})
                campaign_url = product_brief.get("checkout_url") or product_brief.get("sales_url") or ""
                meta = package["meta_plan"]
                campaign_data = {
                    "campaign_name": meta["campaign_name"], "adset_name": f"Público — {product.get('niche') or 'revisar'}",
                    "ad_name": selected_image["name"],
                    "objective": meta["optimization"], "commercial_goal": meta["commercial_goal"],
                    "audience_choice": "broad", "audience": meta["audience"], "placements": meta["placements"],
                    "location": meta["location"], "daily_budget": meta["budget"],
                    "budget": "Definido pelo anunciante; revisar na conta Meta.",
                    "tracking_status": "Não verificado", "conversion_event": "A confirmar manualmente no Events Manager; não verificado.",
                    "tracking_warning": package["tracking_check"]["note"],
                    "url": campaign_url, "creative_id": selected_image["id"], "copy_id": selected_copy["id"],
                    "ads": meta["ads"], "campaign_mode": "agent",
                    "agent_run_id": run_id, "agent_strategy_id": selected_strategy["id"], "agent_strategy_name": selected_strategy["name"],
                    "budget_distribution": package["budget_distribution"], "unit_economics": package["unit_economics"],
                    "tracking_check": package["tracking_check"], "meta_plan": meta,
                    "policy_review": package["policy_check"] | {"checked_at": now}, "policy_checked_at": now,
                    "checklist": {"campaign_reviewed": False, "manual_setup_completed": False},
                    "publishing_notice": "Planejamento somente. A aprovação interna não publica e nenhuma conexão Meta está configurada.",
                }
                conn.execute("INSERT INTO campaigns(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (campaign_id, user_id, project_row["id"], json_dump(campaign_data), now, now))
                conn.execute(
                    "UPDATE projects SET selected_avatar_id=?,selected_angle_id=?,selected_offer_id=?,selected_copy_id=?,selected_creative_id=?,current_step=6,stale_from='',updated_at=? WHERE id=? AND user_id=?",
                    (avatar["id"], angle["id"], package["offers"][0]["id"], selected_copy["id"], selected_image["id"], now, project_row["id"], user_id),
                )
                run_data.update({
                    "status": "ready", "selected_strategy_id": selected_strategy["id"],
                    "selected_strategy": selected_strategy, "selected_at": now,
                    "generated_package": package,
                    "generation_gate": "A escolha foi registrada; ativos e Meta Plan foram criados em modo local para revisão.",
                })
                conn.execute("UPDATE campaign_agent_runs SET status='ready',selected_strategy_id=?,data=?,updated_at=? WHERE id=? AND user_id=?", (selected_strategy["id"], json_dump(run_data), now, run_id, user_id))
                refreshed = conn.execute("SELECT * FROM campaign_agent_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
                updated_project = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_row["id"], user_id)).fetchone()
                return {
                    "run": campaign_agent_run_public(conn, refreshed, user_id),
                    "project": project_public(conn, updated_project),
                    "generated_count": {"avatars": 1, "angles": 1, "offers": len(package["offers"]), "primary_copies": 5, "headlines": 5, "ctas": 5, "image_concepts": 3, "video_concepts": 3, "tests": len(package["test_plan"])},
                    "notice": "Conceitos/prompts e roteiros locais; nenhuma mídia foi renderizada e nenhum anúncio foi publicado.",
                }, 200, []

            if suffix == "chat" and method == "POST":
                message = clean_text(body.get("message"), "Mensagem", 2000, True)
                project_row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (run_row["project_id"], user_id)).fetchone() if run_row["project_id"] else None
                project = project_public(conn, project_row) if project_row else {"metrics": [], "campaign": None}
                now = utc_now()
                user_message = {"role": "user", "text": message, "created_at": now}
                reply = campaign_agent.chat_reply(message, run_data, project)
                reply["created_at"] = utc_now()
                history = list(run_data.get("messages") or []) + [user_message, reply]
                run_data["messages"] = history[-60:]
                run_data["last_chat_at"] = reply["created_at"]
                conn.execute("UPDATE campaign_agent_runs SET data=?,updated_at=? WHERE id=? AND user_id=?", (json_dump(run_data), reply["created_at"], run_id, user_id))
                return {"messages": run_data["messages"], "reply": reply}, 200, []

            if suffix == "improve" and method == "POST":
                project_row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (run_row["project_id"], user_id)).fetchone() if run_row["project_id"] else None
                if not project_row:
                    raise HTTPError(409, "O projeto desta sessão não está disponível.")
                project = project_public(conn, project_row)
                diagnosis = campaign_agent.diagnose_feedback(run_data.get("brief", {}), project)
                saved_test = None
                if diagnosis.get("test"):
                    saved_test = dict(diagnosis["test"])
                    saved_test.update({"id": new_id(), "status": "Pausado", "feedback_source_run_id": run_id})
                    now_test = utc_now()
                    conn.execute("INSERT INTO ab_tests(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (saved_test["id"], user_id, project_row["id"], json_dump(saved_test), now_test, now_test))
                event = {"created_at": utc_now(), "diagnosis": diagnosis, "saved_test": saved_test}
                run_data.setdefault("feedback_events", []).append(event)
                run_data["feedback_events"] = run_data["feedback_events"][-30:]
                conn.execute("UPDATE campaign_agent_runs SET data=?,updated_at=? WHERE id=? AND user_id=?", (json_dump(run_data), event["created_at"], run_id, user_id))
                return {"diagnosis": diagnosis, "test": saved_test, "project": project_public(conn, conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_row["id"], user_id)).fetchone())}, 200, []

            raise HTTPError(405, "Método não permitido para esta sessão.")

        if path == "/api/agent/simulate" and method == "POST":
            run_id = clean_text(body.get("run_id"), "Sessão", 200, True)
            run_row = conn.execute("SELECT * FROM campaign_agent_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
            if not run_row:
                raise HTTPError(404, "Sessão do Campaign Agent não encontrada nesta conta.")
            run_data = json_load(run_row["data"], {}) or {}
            brief = run_data.get("brief", {})
            product = brief.get("product", {})
            simulation = campaign_agent.simulate_scenarios(
                body.get("daily_budget", brief.get("daily_budget")), body.get("days"),
                body.get("price", product.get("price")), body.get("cpa_scenarios"),
                body.get("unit_cost", product.get("unit_cost")), body.get("fees_per_sale", product.get("fees_per_sale")),
            )
            return {"simulation": simulation}, 200, []

        if path == "/api/analyze-url" and method == "POST":
            source_url = clean_text(body.get("url"), "URL", 2000, True)
            try:
                analysis_result = url_analyzer.analyze_url(source_url)
            except ValueError as error:
                missing = url_analyzer.MISSING
                analysis_result = {
                    "source_url": source_url, "analyzed_at": utc_now(), "status": "failed",
                    "status_message": str(error),
                    "fields": {
                        "name": missing, "headline": missing, "subheadline": missing, "description": missing,
                        "benefits": [missing], "price": None, "promo_price": None, "bonuses": [missing],
                        "guarantee": missing, "faq": [missing], "target_audience": [missing], "cta": [missing],
                        "differentiators": [missing], "testimonials": [missing], "relevant_links": [missing],
                        "sales_url": source_url, "checkout_url": "", "niche": missing, "category": missing,
                        "mechanism": missing,
                    },
                    "extraction": {"method": "Nenhuma extração concluída."},
                    "disclaimer": "Não foi possível extrair dados. Nenhuma informação foi inventada; preencha os campos manualmente.",
                }
            created_at = utc_now()
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
            analysis_id = new_id()
            stored_analysis = dict(analysis_result)
            conn.execute("DELETE FROM campaign_agent_page_analyses WHERE expires_at<=?", (created_at,))
            conn.execute(
                "INSERT INTO campaign_agent_page_analyses(id,user_id,requested_url,source_url,data,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",
                (analysis_id, user_id, source_url, str(analysis_result.get("source_url") or source_url)[:2000], json_dump(stored_analysis), expires_at, created_at),
            )
            analysis_result = dict(analysis_result)
            analysis_result["analysis_id"] = analysis_id
            return {"analysis": analysis_result}, 200, []

        if path == "/api/me" and method == "GET":

            return {"user": user}, 200, []

        if path == "/api/me" and method == "PUT":
            name = clean_text(body.get("full_name"), "Nome", 120, True)
            email = clean_text(body.get("email"), "E-mail", 254, True).lower()
            if not email_valid(email):
                raise HTTPError(400, "Informe um e-mail válido.")
            conflict = conn.execute("SELECT 1 FROM users WHERE email=? AND id<>?", (email, user_id)).fetchone()
            if conflict:
                raise HTTPError(409, "Este e-mail já está sendo usado por outra conta.")
            conn.execute("UPDATE users SET full_name=?,email=? WHERE id=?", (name, email, user_id))
            return {"user": {**user, "full_name": name, "email": email}}, 200, []

        if path == "/api/me/security" and method == "PUT":
            current = str(body.get("current_password") or "")
            new_password = str(body.get("new_password") or "")
            row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
            if not verify_password(current, row["password_hash"]):
                raise HTTPError(400, "A senha atual não confere.")
            if len(new_password) < 8:
                raise HTTPError(400, "A nova senha precisa ter pelo menos 8 caracteres.")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
            return {"message": "Senha atualizada."}, 200, []

        if path == "/api/dashboard" and method == "GET":
            counts = {
                "products": conn.execute("SELECT COUNT(*) FROM products WHERE user_id=?", (user_id,)).fetchone()[0],
                "projects": conn.execute("SELECT COUNT(*) FROM projects WHERE user_id=?", (user_id,)).fetchone()[0],
                "offers": conn.execute("SELECT COUNT(*) FROM offers WHERE user_id=?", (user_id,)).fetchone()[0],
                "creatives": conn.execute("SELECT COUNT(*) FROM creatives WHERE user_id=?", (user_id,)).fetchone()[0],
                "campaigns": conn.execute("SELECT COUNT(*) FROM campaigns WHERE user_id=?", (user_id,)).fetchone()[0],
            }
            projects = conn.execute("SELECT p.*,pr.name AS product_name FROM projects p JOIN products pr ON pr.id=p.product_id WHERE p.user_id=? ORDER BY p.updated_at DESC LIMIT 5", (user_id,)).fetchall()
            recent_projects = [dict(row) for row in projects]
            for recent in recent_projects:
                recent["progress_count"] = 1 + sum(bool(conn.execute(f"SELECT 1 FROM {table} WHERE project_id=? AND user_id=? LIMIT 1", (recent["id"], user_id)).fetchone()) for table in ("avatars", "angles", "offers", "copies", "creatives", "campaigns", "metrics"))
            creative_rows = conn.execute("SELECT c.id,c.data,c.created_at,p.id AS project_id,p.name AS project_name FROM creatives c JOIN projects p ON p.id=c.project_id WHERE c.user_id=? ORDER BY c.created_at DESC LIMIT 4", (user_id,)).fetchall()
            recent_creatives = []
            for row in creative_rows:
                item = json_load(row["data"], {})
                recent_creatives.append({"id": row["id"], "name": item.get("name", "Criativo"), "format": item.get("format", ""), "project_id": row["project_id"], "project_name": row["project_name"]})
            campaigns = conn.execute("SELECT ca.data,p.id AS project_id,p.name AS project_name FROM campaigns ca JOIN projects p ON p.id=ca.project_id WHERE ca.user_id=? ORDER BY ca.updated_at DESC LIMIT 4", (user_id,)).fetchall()
            active = []
            for row in campaigns:
                item = json_load(row["data"], {})
                active.append({"project_id": row["project_id"], "project_name": row["project_name"], "campaign_name": item.get("campaign_name", row["project_name"]), "status": "Estrutura pronta"})
            return {"counts": counts, "recent_projects": recent_projects, "recent_creatives": recent_creatives, "campaigns": active}, 200, []

        if path == "/api/products" and method == "GET":
            rows = conn.execute("SELECT * FROM products WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
            return {"products": [dict(row) for row in rows]}, 200, []

        if path == "/api/products" and method == "POST":
            values = product_payload(body)
            product_id, now = new_id(), utc_now()
            columns = list(values.keys())
            conn.execute(
                f"INSERT INTO products(id,user_id,{','.join(columns)},created_at,updated_at) VALUES({','.join(['?']*(len(columns)+4))})",
                [product_id, user_id, *[values[key] for key in columns], now, now],
            )
            store_page_analysis(conn, user_id, product_id, body.get("page_analysis"))
            return {"product": {"id": product_id, "user_id": user_id, **values, "created_at": now, "updated_at": now}}, 201, []

        match = re.fullmatch(r"/api/products/([^/]+)", path)
        if match:
            product_id = match.group(1)
            row = conn.execute("SELECT * FROM products WHERE id=? AND user_id=?", (product_id, user_id)).fetchone()
            if not row:
                raise HTTPError(404, "Produto não encontrado.")
            if method == "PUT":
                values = product_payload(body, dict(row))
                now = utc_now()
                assignments = ",".join(f"{key}=?" for key in values)
                conn.execute(f"UPDATE products SET {assignments},updated_at=? WHERE id=? AND user_id=?", [*values.values(), now, product_id, user_id])
                conn.execute("UPDATE projects SET stale_from='product',updated_at=? WHERE product_id=? AND user_id=?", (now, product_id, user_id))
                updated = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
                return {"product": dict(updated)}, 200, []
            if method == "DELETE":
                conn.execute("DELETE FROM products WHERE id=? AND user_id=?", (product_id, user_id))
                return {"ok": True}, 200, []
            raise HTTPError(405, "Método não permitido.")

        if path == "/api/projects" and method == "GET":
            rows = conn.execute("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
            return {"projects": [project_public(conn, row, full=False) for row in rows]}, 200, []

        if path == "/api/projects" and method == "POST":
            name = clean_text(body.get("name"), "Nome do projeto", 160, True)
            product_id = clean_text(body.get("product_id"), "Produto", 200, True)
            product = conn.execute("SELECT id FROM products WHERE id=? AND user_id=?", (product_id, user_id)).fetchone()
            if not product:
                raise HTTPError(400, "Selecione um produto seu para vincular ao projeto.")
            project_id, now = new_id(), utc_now()
            conn.execute("INSERT INTO projects(id,user_id,product_id,name,status,current_step,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (project_id, user_id, product_id, name, "draft", 0, now, now))
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            return {"project": project_public(conn, row)}, 201, []

        if path == "/api/export" and method == "GET":
            products = [dict(r) for r in conn.execute("SELECT * FROM products WHERE user_id=?", (user_id,)).fetchall()]
            project_rows = conn.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
            projects = [project_public(conn, row) for row in project_rows]
            agent_rows = conn.execute("SELECT * FROM campaign_agent_runs WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
            agent_runs = [campaign_agent_run_public(conn, row, user_id, include_project=False) for row in agent_rows]
            return {"exported_at": utc_now(), "user": user, "products": products, "projects": projects, "campaign_agent_runs": agent_runs}, 200, [("Content-Disposition", "attachment; filename=cakto-ads-ai-export.json")]

        project_match = re.match(r"^/api/projects/([^/]+)(?:/(.*))?$", path)
        if project_match:
            project_id = project_match.group(1)
            suffix = project_match.group(2) or ""
            project_row = require_project(conn, project_id, user_id)

            if suffix == "export" and method == "GET":
                export_format = parse_qs(urlparse(self.path).query).get("format", ["json"])[0].lower()
                export_project = project_public(conn, project_row)
                if export_format == "json":
                    raw = json.dumps(export_project, ensure_ascii=False, indent=2).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                    extension = "json"
                elif export_format == "txt":
                    raw, content_type, extension = exporter.render_txt(export_project), "text/plain; charset=utf-8", "txt"
                elif export_format == "csv":
                    raw, content_type, extension = exporter.render_csv(export_project), "text/csv; charset=utf-8", "csv"
                elif export_format == "pdf":
                    raw, content_type, extension = exporter.render_pdf(export_project), "application/pdf", "pdf"
                else:
                    raise HTTPError(400, "Formato inválido. Use pdf, txt, json ou csv.")
                filename = re.sub(r"[^A-Za-z0-9_-]+", "-", project_row["name"]).strip("-")[:60] or "campanha"
                return raw, 200, [("Content-Type", content_type), ("Content-Disposition", f"attachment; filename={filename}.{extension}")]

            if suffix == "improve" and method == "POST":
                project_data = project_public(conn, project_row)
                proposal = improvement.propose_round(project_data)
                created, now = [], utc_now()
                for item in proposal["tests"]:
                    test_id = new_id()
                    test_data = {**item, "status": "Em teste", "id": test_id, "source": "melhorar-campanha"}
                    conn.execute("INSERT INTO ab_tests(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (test_id, user_id, project_id, json_dump(test_data), now, now))
                    created.append({**test_data, "created_at": now, "updated_at": now})
                return {"proposal": proposal, "tests": created}, 201, []

            if suffix == "creative-version" and method == "POST":
                source_id = clean_text(body.get("creative_id"), "Criativo", 200, True)
                source_row = conn.execute("SELECT * FROM creatives WHERE id=? AND project_id=? AND user_id=?", (source_id, project_id, user_id)).fetchone()
                if not source_row:
                    raise HTTPError(404, "Criativo não encontrado neste projeto.")
                project_data = project_public(conn, project_row)
                source = json_load(source_row["data"], {})
                alternate = next((a for a in project_data.get("angles", []) if a.get("id") != source.get("angle_id") and a.get("top5_recommended")), None)
                alternate = alternate or next((a for a in project_data.get("angles", []) if a.get("id") != source.get("angle_id")), None)
                new_data = dict(source)
                new_id_value = new_id()
                new_data["id"] = new_id_value
                new_data["name"] = f"{source.get('name', 'Criativo')} — Nova versão local"
                if alternate:
                    new_data["angle_id"] = alternate["id"]
                    new_data["angle_name"] = alternate.get("name", "Ângulo alternativo")
                    new_data["hook"] = alternate.get("hook", source.get("hook", ""))
                new_data["visual_direction"] = f"Variação editorial: {source.get('visual_direction', source.get('concept_type', 'conceito'))}"
                new_data["art_direction"] = new_data["visual_direction"]
                new_data["copy_id"] = project_row["selected_copy_id"] or source.get("copy_id")
                copy_item = next((c for c in project_data.get("copies", []) if c.get("id") == new_data["copy_id"]), None)
                if copy_item:
                    new_data["copy_preview"] = copy_item.get("text", "")[:240]
                    new_data["text"] = copy_item.get("text", "")
                product = project_data.get("product") or {}
                prompts = []
                for size, ratio in (("1080x1080", "1:1"), ("1080x1350", "4:5"), ("1080x1920", "9:16")):
                    prompts.append({"size": size, "ratio": ratio, "prompt": f"Variação {size} ({ratio}) do conceito {new_data.get('concept_type','campanha')} para {product.get('name','produto')}. Hook: {new_data.get('hook','')}. Headline: {new_data.get('headline','')}. Recompôr para este formato, respeitar margens, usar somente ativos autorizados e claims confirmados. Sem depoimentos, números, garantias ou resultados inventados."})
                new_data["image_prompts"] = prompts
                new_data["image_prompt"] = prompts[1]["prompt"]
                new_data["media_status"] = "Nova versão de briefing e prompts; nenhum modelo de imagem/vídeo está conectado."
                insert_item(conn, "creatives", user_id, project_id, new_data, new_id_value)
                conn.execute("UPDATE projects SET selected_creative_id=?,stale_from='campaign',updated_at=? WHERE id=?", (new_id_value, utc_now(), project_id))
                saved = conn.execute("SELECT * FROM creatives WHERE id=?", (new_id_value,)).fetchone()
                return {"creative": item_public(saved), "project": project_public(conn, conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())}, 201, []

            if suffix == "policy/check" and method == "POST":
                project_data = project_public(conn, project_row)
                campaign_row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                campaign_data = json_load(campaign_row["data"], {}) if campaign_row else {}
                if body.get("text"):
                    single = policy.check_copy(clean_text(body.get("text"), "Texto", 20000))
                    review = {**single, "ad_count": 1, "checked_at": utc_now()}
                else:
                    review = campaign_policy_review(conn, user_id, project_id, project_data, campaign_data)
                if campaign_row:
                    campaign_data["policy_review"] = review
                    campaign_data["policy_checked_at"] = review.get("checked_at", utc_now())
                    conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(campaign_data), utc_now(), campaign_row["id"]))
                return {"review": review}, 200, []

            if suffix == "policy/correct" and method == "POST":
                project_data = project_public(conn, project_row)
                product_name = (project_data.get("product") or {}).get("name", "")
                copy_id = body.get("copy_id")
                if copy_id:
                    item_row = conn.execute("SELECT * FROM copies WHERE id=? AND project_id=? AND user_id=?", (copy_id, project_id, user_id)).fetchone()
                    if not item_row:
                        raise HTTPError(404, "Copy não encontrada neste projeto.")
                    copy_data = json_load(item_row["data"], {})
                    original_text = copy_data.get("text", "")
                else:
                    original_text = clean_text(body.get("text"), "Texto", 20000)
                    copy_data, item_row = None, None
                correction = policy.suggest_safe_rewrite(original_text, product_name)
                if body.get("apply") and copy_data is not None and correction["changed"]:
                    copy_data["text"] = correction["suggestion"]
                    conn.execute("UPDATE copies SET data=?,updated_at=? WHERE id=?", (json_dump(copy_data), utc_now(), copy_id))
                    mark_stale(conn, project_id, "creatives")
                    campaign_row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                    if campaign_row:
                        campaign_data = json_load(campaign_row["data"], {})
                        campaign_data.pop("policy_review", None)
                        conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(campaign_data), utc_now(), campaign_row["id"]))
                return {"correction": correction, "review": policy.check_copy(correction["suggestion"])}, 200, []

            if suffix == "approve" and method == "POST":
                campaign_row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                if campaign_row and json_load(campaign_row["data"], {}).get("campaign_mode") == "agent" and body.get("explicit_confirmation") is not True:
                    raise HTTPError(400, "Confirmação explícita obrigatória: aprove somente após Policy Check e revisão humana.")
                if not campaign_row:
                    raise HTTPError(400, "Monte a estrutura de campanha antes de aprovar.")
                project_data = project_public(conn, project_row)
                campaign_data = json_load(campaign_row["data"], {})
                review = campaign_policy_review(conn, user_id, project_id, project_data, campaign_data)
                campaign_data["policy_review"] = review
                campaign_data["policy_checked_at"] = review.get("checked_at", utc_now())
                missing = []
                if not project_row["selected_avatar_id"]: missing.append("Defina o avatar recomendado.")
                if not project_row["selected_angle_id"]: missing.append("Defina o ângulo recomendado.")
                if not project_row["selected_offer_id"]: missing.append("Selecione uma oferta.")
                if not project_row["selected_copy_id"]: missing.append("Selecione uma copy.")
                if not project_row["selected_creative_id"]: missing.append("Selecione um criativo.")
                if not campaign_data.get("url"): missing.append("Adicione uma URL de destino.")
                if not campaign_data.get("audience"): missing.append("Defina o público.")
                if not campaign_data.get("daily_budget"): missing.append("Informe o investimento diário.")
                if campaign_data.get("tracking_status") != "Configurado e testado": missing.append("Confirme manualmente que o evento de conversão foi configurado e testado.")
                if not (campaign_data.get("checklist") or {}).get("campaign_reviewed"): missing.append("Marque a revisão manual da campanha no checklist.")
                if len(campaign_data.get("ads", [])) < 3: missing.append("Mantenha ao menos três anúncios na estrutura antes de aprovar.")
                if review.get("risk") != "Baixo": missing.append("Resolva os alertas do Policy Check antes de aprovar.")
                approved = not missing
                if approved:
                    campaign_data["approved_at"] = utc_now()
                    campaign_data["approval_note"] = "Aprovação interna para preparação/configuração. Nenhuma publicação Meta foi realizada."
                else:
                    campaign_data.pop("approved_at", None)
                now = utc_now()
                conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(campaign_data), now, campaign_row["id"]))
                return {"approved": approved, "missing": missing, "review": review, "campaign": campaign_data, "message": "Campanha aprovada para configuração manual; nenhuma publicação foi realizada." if approved else "A campanha ainda precisa de revisão.", "project": project_public(conn, conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())}, 200, []

            if suffix == "automate" and method == "POST":
                existing_campaign = conn.execute("SELECT data FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                if existing_campaign:
                    existing_data = json_load(existing_campaign["data"], {})
                    if existing_data.get("automation_version") == 2:
                        return {"project": project_public(conn, project_row), "already_automated": True}, 200, []
                    raise HTTPError(409, "Este projeto já possui uma campanha. Use um projeto novo ou duplique-o para preservar as edições atuais.")
                existing_count = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()[0] for table in ITEM_TABLES.values())
                if existing_count:
                    raise HTTPError(409, "O modo automático inicia em um projeto vazio para não substituir itens editados. Crie um projeto novo ou use o modo avançado.")
                project_data = project_public(conn, project_row)
                package = automation.build_automatic_package(project_data)
                now = utc_now()
                for kind, table in ITEM_TABLES.items():
                    for item in package[kind]:
                        insert_item(conn, table, user_id, project_id, item, item.get("id"))
                selected = package["selected"]
                conn.execute(
                    "UPDATE projects SET selected_avatar_id=?,selected_angle_id=?,selected_offer_id=?,selected_copy_id=?,selected_creative_id=?,current_step=6,stale_from='',updated_at=? WHERE id=? AND user_id=?",
                    (selected["avatar_id"], selected["angle_id"], selected["offer_id"], selected["copy_id"], selected["creative_id"], now, project_id, user_id),
                )
                conn.execute("INSERT INTO project_insights(id,user_id,project_id,data,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at", (new_id(), user_id, project_id, json_dump(package["xray"]), now))
                conn.execute("INSERT INTO campaigns(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (new_id(), user_id, project_id, json_dump(package["campaign"]), now, now))
                fresh = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
                return {"project": project_public(conn, fresh), "recommendations": package["campaign"]["recommendation_summary"], "provider": package["provider"], "already_automated": False}, 201, []

            if suffix == "" and method == "GET":
                return {"project": project_public(conn, project_row)}, 200, []

            if suffix == "" and method == "PUT":
                updates = {}
                if "name" in body:
                    updates["name"] = clean_text(body["name"], "Nome do projeto", 160, True)
                if "product_id" in body:
                    product_id = clean_text(body["product_id"], "Produto", 200, True)
                    if not conn.execute("SELECT 1 FROM products WHERE id=? AND user_id=?", (product_id, user_id)).fetchone():
                        raise HTTPError(400, "Produto não encontrado nesta conta.")
                    updates["product_id"] = product_id
                    if product_id != project_row["product_id"]:
                        updates["stale_from"] = "product"
                if "current_step" in body:
                    try:
                        step = int(body["current_step"])
                    except (TypeError, ValueError):
                        raise HTTPError(400, "Etapa inválida.")
                    updates["current_step"] = max(0, min(7, step))
                if "status" in body:
                    status_value = clean_text(body["status"], "Status", 40)
                    if status_value not in ("draft", "active", "paused", "complete"):
                        raise HTTPError(400, "Status inválido.")
                    updates["status"] = status_value
                if updates:
                    updates["updated_at"] = utc_now()
                    conn.execute("UPDATE projects SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=? AND user_id=?", [*updates.values(), project_id, user_id])
                row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                return {"project": project_public(conn, row)}, 200, []

            if suffix == "" and method == "DELETE":
                conn.execute("DELETE FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
                return {"ok": True}, 200, []

            if suffix == "duplicate" and method == "POST":
                old_project = project_public(conn, project_row)
                now, clone_id = utc_now(), new_id()
                new_name = clean_text(body.get("name") or f"Cópia — {old_project['name']}", "Nome da cópia", 160, True)
                conn.execute(
                    "INSERT INTO projects(id,user_id,product_id,name,status,current_step,stale_from,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (clone_id, user_id, old_project["product_id"], new_name, "draft", old_project["current_step"], "Revisar etapas", now, now),
                )
                mapping = {}
                for kind, table in ITEM_TABLES.items():
                    for old_item in old_project.get(kind, []):
                        new_item_id = new_id()
                        mapping[old_item["id"]] = new_item_id
                        copied = deep_remap(old_item, mapping)
                        copied["id"] = new_item_id
                        copied.pop("created_at", None)
                        copied.pop("updated_at", None)
                        insert_item(conn, table, user_id, clone_id, copied, new_item_id)
                selection_updates = {}
                for key in ("selected_avatar_id", "selected_angle_id", "selected_offer_id", "selected_copy_id", "selected_creative_id"):
                    value = old_project.get(key)
                    selection_updates[key] = mapping.get(value) if value else None
                conn.execute("UPDATE projects SET " + ",".join(f"{key}=?" for key in selection_updates) + " WHERE id=?", [*selection_updates.values(), clone_id])
                if old_project.get("xray"):
                    conn.execute("INSERT INTO project_insights(id,user_id,project_id,data,updated_at) VALUES(?,?,?,?,?)", (new_id(), user_id, clone_id, json_dump(old_project["xray"]), now))
                if old_project.get("campaign"):
                    data = deep_remap(old_project["campaign"], mapping)
                    data.pop("id", None)
                    data.pop("updated_at", None)
                    conn.execute("INSERT INTO campaigns(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (new_id(), user_id, clone_id, json_dump(data), now, now))
                for metric in old_project.get("metrics", []):
                    data = dict(metric); data.pop("id", None); data.pop("created_at", None)
                    conn.execute("INSERT INTO metrics(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (new_id(), user_id, clone_id, json_dump(data), now, now))
                for test in old_project.get("tests", []):
                    data = deep_remap(test, mapping); data.pop("id", None); data.pop("created_at", None); data.pop("updated_at", None)
                    conn.execute("INSERT INTO ab_tests(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (new_id(), user_id, clone_id, json_dump(data), now, now))
                new_row = conn.execute("SELECT * FROM projects WHERE id=?", (clone_id,)).fetchone()
                return {"project": project_public(conn, new_row)}, 201, []

            if suffix == "select" and method in ("PUT", "PATCH"):
                kind = str(body.get("kind") or "")
                if kind not in SELECTIONS:
                    raise HTTPError(400, "Seleção inválida.")
                column, table, stale = SELECTIONS[kind]
                item_id = body.get("id")
                if item_id:
                    if not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND project_id=? AND user_id=?", (str(item_id), project_id, user_id)).fetchone():
                        raise HTTPError(400, "O item selecionado não pertence a este projeto.")
                conn.execute(f"UPDATE projects SET {column}=?,stale_from=?,updated_at=? WHERE id=?", (item_id, stale, utc_now(), project_id))
                row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                return {"project": project_public(conn, row)}, 200, []

            generate_match = re.fullmatch(r"generate/(avatars|angles|offers|copies|creatives)", suffix)
            if generate_match and method == "POST":
                stage = generate_match.group(1)
                project = project_public(conn, project_row)
                generated = generator.generate(stage, project)
                if stage == "avatars":
                    existing_count = conn.execute("SELECT COUNT(*) FROM avatars WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()[0]
                    if existing_count >= 5:
                        raise HTTPError(409, "Este projeto já tem cinco avatares. Exclua um perfil antes de gerar outro.")
                    generated = generated[existing_count:5]
                table = ITEM_TABLES[stage]
                for data in generated:
                    insert_item(conn, table, user_id, project_id, data, data.get("id"))
                stale_map = {"avatars": "angles", "angles": "offers", "offers": "copies", "copies": "creatives", "creatives": "campaign"}
                mark_stale(conn, project_id, stale_map[stage])
                row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                return {"project": project_public(conn, row), "generated_count": len(generated), "provider": generator.PROVIDER}, 201, []

            items_match = re.fullmatch(r"items/(avatars|angles|offers|copies|creatives)(?:/([^/]+))?", suffix)
            if items_match:
                kind, item_id = items_match.group(1), items_match.group(2)
                table = ITEM_TABLES[kind]
                if not item_id and method == "POST":
                    data = body.get("data")
                    if not isinstance(data, dict):
                        raise HTTPError(400, "Envie os dados do item em 'data'.")
                    created_id = new_id()
                    insert_item(conn, table, user_id, project_id, data, created_id)
                    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (created_id,)).fetchone()
                    return {"item": item_public(row)}, 201, []
                if item_id:
                    row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND project_id=? AND user_id=?", (item_id, project_id, user_id)).fetchone()
                    if not row:
                        raise HTTPError(404, "Item não encontrado neste projeto.")
                    if method == "PUT":
                        current = json_load(row["data"], {})
                        patch = body.get("data", body)
                        if not isinstance(patch, dict):
                            raise HTTPError(400, "Dados do item inválidos.")
                        current.update(patch)
                        current["id"] = item_id
                        conn.execute(f"UPDATE {table} SET data=?,updated_at=? WHERE id=? AND user_id=?", (json_dump(current), utc_now(), item_id, user_id))
                        stale_map = {"avatars": "angles", "angles": "offers", "offers": "copies", "copies": "creatives", "creatives": "campaign"}
                        mark_stale(conn, project_id, stale_map[kind])
                        saved = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
                        return {"item": item_public(saved)}, 200, []
                    if method == "DELETE":
                        conn.execute(f"DELETE FROM {table} WHERE id=? AND project_id=? AND user_id=?", (item_id, project_id, user_id))
                        selection_column = {"avatars": "selected_avatar_id", "angles": "selected_angle_id", "offers": "selected_offer_id", "copies": "selected_copy_id", "creatives": "selected_creative_id"}[kind]
                        conn.execute(f"UPDATE projects SET {selection_column}=CASE WHEN {selection_column}=? THEN NULL ELSE {selection_column} END,updated_at=? WHERE id=?", (item_id, utc_now(), project_id))
                        return {"ok": True}, 200, []
                raise HTTPError(405, "Método não permitido.")

            if suffix == "campaign/generate" and method == "POST":
                project = project_public(conn, project_row)
                product = project.get("product") or {}
                creative = selected_data(project, "selected_creative_id", "creatives")
                copy = selected_data(project, "selected_copy_id", "copies")
                url = product.get("checkout_url") or product.get("sales_url") or ""
                ad_creatives = (project.get("creatives") or [])[:3]
                primary_copies = [item for item in (project.get("copies") or []) if item.get("type") == "primary"]
                if not primary_copies and copy:
                    primary_copies = [copy]
                ads = []
                for index in range(3):
                    asset = ad_creatives[index] if index < len(ad_creatives) else (creative or {})
                    chosen_copy = primary_copies[index % len(primary_copies)] if primary_copies else {}
                    concept_name = asset.get("name") or f"Conceito {index + 1:02d} — definir"
                    ads.append({"id": new_id(), "ad_name": f"Anúncio {index + 1:02d} — {concept_name}", "creative_id": asset.get("id"), "copy_id": chosen_copy.get("id"), "hook": asset.get("hook", ""), "headline": asset.get("headline", ""), "description": "", "cta": asset.get("cta", ""), "url": url})
                target_price = product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price")
                contribution = round(float(target_price) - float(product.get("unit_cost")), 2) if target_price not in (None, "") and product.get("unit_cost") not in (None, "") else None
                data = {
                    "campaign_name": project["name"],
                    "adset_name": f"Público — {product.get('niche') or 'definir nicho'}",
                    "ad_name": (creative or {}).get("name", "Anúncio — selecione um criativo"),
                    "objective": "Vendas somente após confirmar e testar o evento Purchase; rastreamento não verificado.",
                    "commercial_goal": "Vendas", "audience_choice": "broad",
                    "audience": product.get("target_audience") or "Definir público com base em pesquisa e políticas aplicáveis.",
                    "audiences": [
                        {"key": "broad", "name": "Público amplo", "recommendation": "Candidato inicial; validar com dados da conta.", "eligible": True},
                        {"key": "interests", "name": "Interesses", "recommendation": f"Testar temas relacionados a {product.get('niche') or 'nicho não identificado'}; confirmar disponibilidade na conta.", "eligible": bool(product.get("niche"))},
                        {"key": "remarketing", "name": "Remarketing", "recommendation": "Não recomendado até validar público elegível, consentimento e tamanho.", "eligible": False},
                        {"key": "lookalike", "name": "Lookalike", "recommendation": "Não recomendado; volume/qualidade da fonte não verificados.", "eligible": False},
                    ],
                    "placements": "Sugestão: avaliar Advantage+ e revisar prévias por formato.",
                    "budget": "A definir pelo anunciante — não informado.", "daily_budget": None,
                    "location": "Definir manualmente conforme produto e políticas.",
                    "tracking_status": "Não verificado", "conversion_event": "Purchase — confirmar e testar no Events Manager",
                    "tracking_warning": "Este app não consultou seu Pixel/Dataset.",
                    "creative_id": (creative or {}).get("id"), "copy_id": (copy or {}).get("id"), "url": url,
                    "ads": ads, "campaign_mode": "advanced", "checklist": {"campaign_reviewed": False, "manual_setup_completed": False},
                    "break_even_cpa": contribution,
                    "break_even_roas": round(float(target_price) / contribution, 2) if contribution and contribution > 0 else None,
                    "publishing_notice": "Estrutura de planejamento apenas. Não há publicação automática nem integração Meta nesta fase.",
                }
                data["policy_review"] = campaign_policy_review(conn, user_id, project_id, project, data)
                data["policy_checked_at"] = data["policy_review"].get("checked_at")
                old = conn.execute("SELECT id FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                now = utc_now()
                if old:
                    campaign_id = old["id"]
                    conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(data), now, campaign_id))
                else:
                    campaign_id = new_id()
                    conn.execute("INSERT INTO campaigns(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (campaign_id, user_id, project_id, json_dump(data), now, now))
                conn.execute("UPDATE projects SET stale_from=CASE WHEN stale_from='campaign' THEN '' ELSE stale_from END,updated_at=? WHERE id=?", (now, project_id))
                return {"campaign": {**data, "id": campaign_id, "updated_at": now}}, 200, []

            if suffix == "campaign" and method == "GET":
                row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                return {"campaign": json_load(row["data"], {}) | {"id": row["id"], "updated_at": row["updated_at"]} if row else None}, 200, []

            if suffix == "campaign" and method in ("PUT", "POST"):
                row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                existing = json_load(row["data"], {}) if row else {}
                data = dict(existing)
                allowed = (
                    "campaign_name", "adset_name", "ad_name", "objective", "commercial_goal", "audience", "audience_choice",
                    "placements", "budget", "creative_id", "copy_id", "url", "location", "tracking_status",
                    "conversion_event", "campaign_mode",
                )
                for key in allowed:
                    if key in body:
                        data[key] = clean_text(body[key], key.replace("_", " ").capitalize(), 5000)
                if "daily_budget" in body:
                    data["daily_budget"] = price_value(body.get("daily_budget"), "Orçamento diário")
                if "checklist" in body:
                    patch_checklist = body.get("checklist")
                    if not isinstance(patch_checklist, dict) or any(k not in ("campaign_reviewed", "manual_setup_completed") for k in patch_checklist):
                        raise HTTPError(400, "Checklist inválido.")
                    current_checklist = dict(data.get("checklist") or {})
                    current_checklist.update({k: bool(v) for k, v in patch_checklist.items()})
                    data["checklist"] = current_checklist
                if "audience_choice" in body and data["audience_choice"] not in ("broad", "interests", "remarketing", "lookalike", "custom"):
                    raise HTTPError(400, "Tipo de público inválido.")
                if "tracking_status" in body and data["tracking_status"] not in ("Não verificado", "Configurado e testado", "Sem pixel/evento"):
                    raise HTTPError(400, "Status do rastreamento inválido.")
                if "commercial_goal" in body and data["commercial_goal"] not in ("Vendas", "Leads", "Mensagens", "Tráfego", "Reconhecimento", "Validar produto", "Outro"): 
                    raise HTTPError(400, "Objetivo comercial inválido.")
                if "ads" in body:
                    ads = body.get("ads")
                    if not isinstance(ads, list) or len(ads) > 20:
                        raise HTTPError(400, "A lista de anúncios deve conter até 20 itens.")
                    normalized_ads = []
                    for index, ad in enumerate(ads):
                        if not isinstance(ad, dict):
                            raise HTTPError(400, "Anúncio inválido.")
                        creative_ref, copy_ref = ad.get("creative_id"), ad.get("copy_id")
                        if creative_ref and not conn.execute("SELECT 1 FROM creatives WHERE id=? AND project_id=? AND user_id=?", (creative_ref, project_id, user_id)).fetchone():
                            raise HTTPError(400, "O criativo do anúncio não pertence a este projeto.")
                        if copy_ref and not conn.execute("SELECT 1 FROM copies WHERE id=? AND project_id=? AND user_id=?", (copy_ref, project_id, user_id)).fetchone():
                            raise HTTPError(400, "A copy do anúncio não pertence a este projeto.")
                        normalized_ads.append({
                            "id": clean_text(ad.get("id") or new_id(), "ID do anúncio", 100),
                            "ad_name": clean_text(ad.get("ad_name") or f"Anúncio {index + 1:02d}", "Nome do anúncio", 180),
                            "creative_id": creative_ref or None, "copy_id": copy_ref or None,
                            "hook": clean_text(ad.get("hook"), "Hook", 4000),
                            "headline": clean_text(ad.get("headline"), "Headline", 2000),
                            "description": clean_text(ad.get("description"), "Descrição", 2000),
                            "cta": clean_text(ad.get("cta"), "CTA", 300),
                            "url": clean_text(ad.get("url") or data.get("url"), "URL", 2000),
                        })
                    data["ads"] = normalized_ads
                for key, table in (("creative_id", "creatives"), ("copy_id", "copies")): 
                    ref = data.get(key)
                    if ref and not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND project_id=? AND user_id=?", (ref, project_id, user_id)).fetchone():
                        raise HTTPError(400, f"{key} não pertence a este projeto.")
                data["publishing_notice"] = "Estrutura de planejamento apenas. Não há publicação automática nem integração Meta nesta fase."
                now = utc_now()
                if row:
                    campaign_id = row["id"]
                    conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(data), now, campaign_id))
                else:
                    campaign_id = new_id()
                    conn.execute("INSERT INTO campaigns(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (campaign_id, user_id, project_id, json_dump(data), now, now))
                conn.execute("UPDATE projects SET selected_creative_id=?,selected_copy_id=?,stale_from=CASE WHEN stale_from='campaign' THEN '' ELSE stale_from END,updated_at=? WHERE id=?", (data.get("creative_id") or None, data.get("copy_id") or None, now, project_id))
                return {"campaign": {**data, "id": campaign_id, "updated_at": now}}, 200, []

            if suffix == "metrics" and method == "GET":
                rows = conn.execute("SELECT * FROM metrics WHERE project_id=? AND user_id=? ORDER BY created_at DESC,id DESC", (project_id, user_id)).fetchall()
                metrics_list = [json_load(row["data"], {}) | {"id": row["id"], "created_at": row["created_at"]} for row in rows]
                return {"metrics": metrics_list, "diagnosis": analytics.diagnose_metrics(metrics_list[0] if metrics_list else None)}, 200, []

            if suffix == "metrics" and method == "POST":
                values = analytics.calculate_metrics(body)
                if not values["date"]:
                    values["date"] = date.today().isoformat()
                try:
                    date.fromisoformat(values["date"])
                except ValueError:
                    raise HTTPError(400, "Informe a data no formato AAAA-MM-DD.")
                for field, table, default_column in (("creative_id", "creatives", "selected_creative_id"), ("angle_id", "angles", "selected_angle_id"), ("offer_id", "offers", "selected_offer_id"), ("copy_id", "copies", "selected_copy_id")):
                    reference = body.get(field) or project_row[default_column]
                    if reference and not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND project_id=? AND user_id=?", (reference, project_id, user_id)).fetchone():
                        raise HTTPError(400, f"{field} não pertence a este projeto.")
                    values[field] = reference
                values["audience_segment"] = clean_text(body.get("audience_segment"), "Segmento de público", 200) if body.get("audience_segment") else ""
                metric_id, now = new_id(), utc_now()
                conn.execute("INSERT INTO metrics(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (metric_id, user_id, project_id, json_dump(values), now, now))
                return {"metric": {**values, "id": metric_id, "created_at": now}, "diagnosis": analytics.diagnose_metrics(values)}, 201, []

            if suffix == "tests" and method == "GET":
                rows = conn.execute("SELECT * FROM ab_tests WHERE project_id=? AND user_id=? ORDER BY created_at DESC,id DESC", (project_id, user_id)).fetchall()
                tests = [json_load(row["data"], {}) | {"id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]
                return {"tests": tests}, 200, []

            if suffix == "tests" and method == "POST":
                data = self._test_payload(body)
                test_id, now = new_id(), utc_now()
                data["id"] = test_id
                conn.execute("INSERT INTO ab_tests(id,user_id,project_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?)", (test_id, user_id, project_id, json_dump(data), now, now))
                return {"test": {**data, "created_at": now, "updated_at": now}}, 201, []

            test_match = re.fullmatch(r"tests/([^/]+)", suffix)
            if test_match:
                test_id = test_match.group(1)
                row = conn.execute("SELECT * FROM ab_tests WHERE id=? AND project_id=? AND user_id=?", (test_id, project_id, user_id)).fetchone()
                if not row:
                    raise HTTPError(404, "Teste não encontrado.")
                if method == "PUT":
                    data = json_load(row["data"], {})
                    data.update(self._test_payload(body, existing=data))
                    data["id"] = test_id
                    now = utc_now()
                    conn.execute("UPDATE ab_tests SET data=?,updated_at=? WHERE id=?", (json_dump(data), now, test_id))
                    return {"test": {**data, "updated_at": now}}, 200, []
                if method == "DELETE":
                    conn.execute("DELETE FROM ab_tests WHERE id=? AND user_id=?", (test_id, user_id))
                    return {"ok": True}, 200, []
                raise HTTPError(405, "Método não permitido.")

        if path == "/api/policy/check" and method == "POST":
            text = clean_text(body.get("text"), "Texto", 20000)
            review = policy.check_copy(text) | {"checked_at": utc_now()}
            project_id = body.get("project_id")
            if project_id:
                require_project(conn, str(project_id), user_id)
                campaign_row = conn.execute("SELECT * FROM campaigns WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
                if campaign_row:
                    campaign_data = json_load(campaign_row["data"], {})
                    campaign_data["policy_review"] = review
                    campaign_data["policy_checked_at"] = review["checked_at"]
                    conn.execute("UPDATE campaigns SET data=?,updated_at=? WHERE id=?", (json_dump(campaign_data), utc_now(), campaign_row["id"]))
            return {"review": review}, 200, []

        if path == "/api/policy/correct" and method == "POST":
            text = clean_text(body.get("text"), "Texto", 20000)
            product_name = clean_text(body.get("product_name"), "Produto", 200) if body.get("product_name") else ""
            project_id = body.get("project_id")
            if project_id:
                project = project_public(conn, require_project(conn, str(project_id), user_id), full=False)
                product_name = (project.get("product") or {}).get("name", product_name)
            correction = policy.suggest_safe_rewrite(text, product_name)
            return {"correction": correction, "review": policy.check_copy(correction["suggestion"])}, 200, []

        if path == "/api/learning" and method == "GET":
            return {"report": learning.build_report(conn, user_id)}, 200, []

        raise HTTPError(404, "Rota da API não encontrada.")

    def _test_payload(self, body, existing=None):
        old = existing or {}
        fields = {}
        for key, label, limit in (("name", "Nome do teste", 160), ("type", "Tipo", 60), ("variant_a", "Variação A", 4000), ("variant_b", "Variação B", 4000), ("hypothesis", "Hipótese", 3000)):
            value = body[key] if key in body else old.get(key, "")
            fields[key] = clean_text(value, label, limit, key in ("name", "type"))
        status_value = body.get("status", old.get("status", "Em teste"))
        if status_value not in ("Em teste", "Vencedor", "Perdedor", "Pausado"):
            raise HTTPError(400, "Status de teste inválido.")
        fields["status"] = status_value
        return fields

    def _cookie_attributes(self):
        """Choose cookie flags that work locally and in HTTPS-embedded previews.

        Arena previews are served from an HTTPS .e2b.app host inside a cross-site
        iframe. Lax cookies are not sent from that context; use a secure,
        partitioned cross-site cookie there while retaining Lax for local HTTP.
        """
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        host_values = [
            self.headers.get("Host", ""),
            self.headers.get("X-Forwarded-Host", ""),
            self.headers.get("X-Original-Host", ""),
            self.headers.get("X-Forwarded-Server", ""),
        ]
        origin_values = [
            self.headers.get("Origin", ""),
            self.headers.get("Referer", ""),
        ]

        def hostnames(values):
            result = []
            for value in values:
                for candidate in value.split(","):
                    candidate = candidate.strip()
                    if not candidate:
                        continue
                    parsed = urlparse(candidate if "://" in candidate else f"//{candidate}")
                    if parsed.hostname:
                        result.append(parsed.hostname.lower())
            return result

        request_hosts = hostnames(host_values)
        origin_hosts = hostnames(origin_values)
        preview_context = any(
            host == "e2b.app" or host.endswith(".e2b.app")
            for host in (*request_hosts, *origin_hosts)
        )
        forwarded_https = forwarded_proto == "https"
        origin_https = any(urlparse(value).scheme.lower() == "https" for value in origin_values)
        secure_context = preview_context or forwarded_https or origin_https
        secure = os.environ.get("COOKIE_SECURE", "0") == "1" or secure_context
        configured_same_site = os.environ.get("COOKIE_SAMESITE", "").strip().capitalize()
        if preview_context:
            # Preview hostnames may be hidden in forwarding headers or Referer;
            # force CHIPS attributes regardless of local cookie overrides.
            same_site = "None"
        elif configured_same_site in {"Lax", "Strict", "None"}:
            same_site = configured_same_site
        else:
            same_site = "None" if secure_context else "Lax"
        if same_site == "None":
            secure = True
        partitioned = os.environ.get("COOKIE_PARTITIONED", "0") == "1" or preview_context
        attributes = ["Path=/", "HttpOnly", f"SameSite={same_site}"]
        if secure:
            attributes.append("Secure")
        if partitioned and secure and same_site == "None":
            attributes.append("Partitioned")
        return "; ".join(attributes)

    def _session_cookie(self, raw):
        cookie = f"cakto_session={raw}; {self._cookie_attributes()}; Max-Age=1209600"
        return [("Set-Cookie", cookie)]

    def _clear_cookie(self):
        cookie = f"cakto_session=; {self._cookie_attributes()}; Max-Age=0"
        return [("Set-Cookie", cookie)]
