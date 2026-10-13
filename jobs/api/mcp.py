"""MCP server for AI agents: the same verified job data, as tools.

Streamable HTTP transport, stateless: POST /mcp/ with one JSON-RPC message, JSON back. No sessions,
no server-initiated streams (GET answers 405), which is all a read-only tool server needs. An API key
(Authorization: Bearer ri_...) raises limits and returns the enriched fields; without one the anonymous
tier applies.
"""
import json

from django.http import HttpResponse, JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from jobs.api import serialize, usage
from jobs.api.auth import RateLimited, identify
from jobs.api.rest import ATTRIBUTION, CORS, search

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "remote-impact-jobs", "version": "1.0.0", "title": "Remote Impact Jobs",
          "description": "Verified remote jobs at climate, global health, AI safety and other impact organizations: search by area, "
                         "the applicant's country, level and pay. Checked daily against each employer's own job board.",
          "websiteUrl": "https://remoteimpact.org/developers/"}
INSTRUCTIONS = (
    "Remote roles at climate, global health, AI safety, education and other impact organizations, checked daily "
    "against each employer's own job board. Use search_jobs with the person's constraints (country they live in, "
    "area, level, minimum pay); eligible_countries and remote_scope say who can apply. Always give the listing url. "
    + ATTRIBUTION
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_ORG = {"type": "object", "properties": {"name": {"type": "string"}, "slug": {"type": "string"}, "url": {"type": ["string", "null"]},
                                         "homepage_url": {"type": ["string", "null"]}, "careers_url": {"type": ["string", "null"]}}}
_JOB = {"type": "object", "description": "One verified open role", "properties": {
    "id": {"type": "integer"}, "slug": {"type": "string"}, "title": {"type": "string"}, "organization": _ORG,
    "location": {"type": "string"}, "remote_scope": {"type": "string", "enum": ["worldwide", "countries", "region", "unspecified"]},
    "eligible_countries": {"type": "array", "items": {"type": "string"}}, "job_type": {"type": "string"},
    "experience_level": {"type": ["string", "null"]},
    "salary": {"type": ["object", "null"], "properties": {"min": {"type": ["number", "null"]}, "max": {"type": ["number", "null"]},
                                                          "currency": {"type": "string"}, "period": {"type": "string"}}},
    "posted_at": {"type": ["string", "null"]}, "verification": {"type": "object"}, "url": {"type": "string"}, "apply_url": {"type": "string"},
    "description_excerpt": {"type": "string"}}, "required": ["id", "title", "url"]}
TOOLS = [
    {
        "name": "search_jobs",
        "title": "Search open remote impact roles",
        "description": "Find open, verified remote roles. Filter by text, impact area, the applicant's country, job type, "
                       "level, minimum annual pay and recency. Returns the best matches with eligibility, pay and links "
                       "(5 per call without a key, 10 with a free key, 25 with commercial access).",
        "annotations": READ_ONLY,
        "outputSchema": {"type": "object", "properties": {"total": {"type": "integer"}, "jobs": {"type": "array", "items": _JOB}}, "required": ["total", "jobs"]},
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Free text: role, skills, organization"},
            "area": {"type": "string", "description": "Impact area slug from list_areas, e.g. climate-environment"},
            "country": {"type": "string", "description": "Country the applicant lives in, e.g. Kenya"},
            "job_type": {"type": "string", "enum": ["full-time", "part-time", "contract", "freelance", "internship"]},
            "level": {"type": "string", "enum": ["internship", "entry", "mid", "senior", "executive"]},
            "min_salary": {"type": "integer", "description": "Minimum annual pay (listing currency)"},
            "posted_within_days": {"type": "integer", "minimum": 1, "maximum": 90},
            "worldwide_only": {"type": "boolean", "description": "Only roles open from anywhere"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
        }},
    },
    {
        "name": "get_job",
        "title": "Get one role",
        "description": "Full record for one open role by slug or id, including verification and eligibility.",
        "annotations": READ_ONLY,
        "outputSchema": {"type": "object", "properties": {"job": {"oneOf": [_JOB, {"type": "null"}]}}, "required": ["job"]},
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "Job slug or numeric id"}},
                        "required": ["id"]},
    },
    {
        "name": "list_areas",
        "title": "List impact areas",
        "description": "Impact areas (categories) with the number of open roles in each.",
        "annotations": READ_ONLY,
        "outputSchema": {"type": "object", "properties": {"areas": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "slug": {"type": "string"}, "open_roles": {"type": "integer"}, "url": {"type": "string"}}}}}, "required": ["areas"]},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_organization",
        "title": "Get an organization and its open roles",
        "description": "Organization details (homepage, careers page) and its open roles on Remote Impact.",
        "annotations": READ_ONLY,
        "outputSchema": {"type": "object", "properties": {"organization": {"oneOf": [_ORG, {"type": "null"}]}, "jobs": {"type": "array", "items": _JOB}},
                         "required": ["organization", "jobs"]},
        "inputSchema": {"type": "object", "properties": {"slug": {"type": "string", "description": "Organization slug, e.g. from a job's organization.slug"}}, "required": ["slug"]},
    },
]


def _error(id_, code, message, status=200):
    return _reply({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}, status)


def _reply(payload, status=200, caller=None):
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    for key, value in CORS.items():
        response[key] = value
    response["Cache-Control"] = "no-store"
    if caller is not None:
        response["X-RateLimit-Tier"] = caller.tier
    return response


def _summary(job):
    pay = job["salary"]
    pay_text = f"{pay['currency']} {int(pay['min'] or 0):,}–{int(pay['max'] or 0):,}/{pay['period']}" if pay else "pay not stated"
    where = ", ".join(job["eligible_countries"]) if job["eligible_countries"] else job["remote_scope"]
    return f"{job['title']} at {job['organization']['name']} ({job['location']}; open to: {where}; {pay_text}) {job['url']}"


def _call(name, args, caller):
    enriched = caller.limits["enriched"]
    if name == "search_jobs":
        params = QueryDict(mutable=True)
        mapping = {"query": "q", "area": "area", "country": "country", "job_type": "job_type", "level": "level",
                   "min_salary": "min_salary", "limit": "limit"}
        for key, target in mapping.items():
            if args.get(key) not in (None, ""):
                params[target] = str(args[key])
        if args.get("posted_within_days"):
            params["posted_since"] = f"{int(args['posted_within_days'])}d"
        if args.get("worldwide_only"):
            params["remote_scope"] = "worldwide"
        cap = caller.limits["mcp_max"]
        rows, total, _, limit = search(params, caller, limit=min(int(args.get("limit") or cap), cap))
        jobs = [serialize.job_record(j, enriched=enriched) for j in rows]
        note = ("" if enriched else f" (this tier returns up to {cap} per search; commercial keys return 25 with enriched fields: "
                                    f"{serialize._site()}/developers/)")
        text = (f"{total} open roles match; showing {len(jobs)}{note}.\n" + "\n".join(f"- {_summary(j)}" for j in jobs)
                if jobs else "No open roles match these filters. Try fewer filters or a broader area.")
        return text, {"total": total, "jobs": jobs}
    if name == "get_job":
        from jobs.services.job_service import JobService

        ident = str(args.get("id", "")).strip()
        qs = JobService.get_available_jobs().select_related("organization", "category")
        job = qs.filter(slug=ident).first() or (qs.filter(pk=int(ident)).first() if ident.isdigit() else None)
        if job is None:
            return "No open role with that id (it may have closed).", {"job": None}
        record = serialize.job_record(job, enriched=enriched)
        return _summary(record) + "\n\n" + record["description_excerpt"], {"job": record}
    if name == "list_areas":
        areas = serialize.category_records()
        return "\n".join(f"- {a['name']} ({a['slug']}): {a['open_roles']} open roles" for a in areas), {"areas": areas}
    if name == "get_organization":
        from jobs.models import Organization
        from jobs.services.job_service import JobService

        org = Organization.objects.filter(slug=str(args.get("slug", "")).strip()).first()
        if org is None:
            return "No organization with that slug.", {"organization": None}
        roles = JobService.get_available_jobs().filter(organization=org).select_related("organization", "category").order_by("-posted_at")[:25]
        jobs = [serialize.job_record(j, enriched=enriched) for j in roles]
        record = serialize.organization_record(org, with_counts=True)
        text = f"{org.name}: {record['open_roles']} open roles. {record['homepage_url'] or ''}\n" + "\n".join(f"- {_summary(j)}" for j in jobs)
        return text, {"organization": record, "jobs": jobs}
    raise KeyError(name)


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE", "OPTIONS"])
def endpoint(request):
    if request.method == "OPTIONS":
        return _reply({}, status=204)
    if request.method == "GET":
        response = HttpResponse("This MCP server does not open server-initiated streams; send JSON-RPC via POST.",
                                status=405, content_type="text/plain")
        response["Allow"] = "POST, DELETE, OPTIONS"
        return response
    if request.method == "DELETE":
        return HttpResponse(status=204)
    try:
        message = json.loads(request.body[:20000] or b"")
    except (ValueError, UnicodeDecodeError):
        return _error(None, -32700, "Parse error", status=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(None, -32600, "Invalid request", status=400)
    method, id_, params = message["method"], message.get("id"), message.get("params") or {}
    if id_ is None:  # notification
        return HttpResponse(status=202)
    try:
        caller = identify(request)
    except RateLimited as exc:
        response = _error(id_, -32000, "Rate limited; add an API key from remoteimpact.org/developers/ for higher limits.", status=429)
        response["Retry-After"] = str(exc.retry_after)
        return response
    usage.record(request, caller, "mcp", (params.get("name") if method == "tools/call" else method) or method)
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _reply({"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER, "instructions": INSTRUCTIONS}}, caller=caller)
    if method == "ping":
        return _reply({"jsonrpc": "2.0", "id": id_, "result": {}}, caller=caller)
    if method == "tools/list":
        return _reply({"jsonrpc": "2.0", "id": id_, "result": {"tools": TOOLS}}, caller=caller)
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments must be an object")
        try:
            text, structured = _call(name, args, caller)
        except KeyError:
            return _error(id_, -32602, f"Unknown tool: {name}")
        except (TypeError, ValueError) as exc:
            return _reply({"jsonrpc": "2.0", "id": id_, "result": {
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}], "isError": True}}, caller=caller)
        return _reply({"jsonrpc": "2.0", "id": id_, "result": {
            "content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}}, caller=caller)
    if method in ("resources/list", "prompts/list"):
        return _reply({"jsonrpc": "2.0", "id": id_, "result": {method.split("/")[0]: []}}, caller=caller)
    return _error(id_, -32601, f"Method not found: {method}")


def server_card(request):
    """Static description of the server for registries and scanners (no handshake needed)."""
    card = {"serverInfo": SERVER, "authentication": {"required": False, "schemes": ["bearer"],
                                                      "description": "Optional API key for higher limits: https://remoteimpact.org/developers/"},
            "instructions": INSTRUCTIONS, "tools": TOOLS, "resources": [], "prompts": []}
    response = JsonResponse(card, json_dumps_params={"ensure_ascii": False})
    for key, value in CORS.items():
        response[key] = value
    response["Cache-Control"] = "public, max-age=3600"
    return response
