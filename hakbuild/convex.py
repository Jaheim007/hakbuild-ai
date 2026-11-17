"""Generation des schemas et fonctions Convex (application + plateforme).

Deux niveaux, conformement au PRD section 8 :
  - niveau 1 : le projet Convex de la plateforme HakBuild (platform_schema_ts) ;
  - niveau 2 : un projet Convex dedie par application generee (app_schema_ts).
"""
from __future__ import annotations

import re

NUMERIC_HINTS = ("amount", "price", "total", "stock", "quantity", "lat", "lng", "rating", "count", "duration")
BOOL_HINTS = ("is", "has", "enabled", "active", "archived")


def _fields(spec: str) -> list[str]:
    return [f.strip() for f in spec.split(",") if f.strip()]


def ts_validator(field: str) -> str:
    low = field.lower()
    if low.endswith("id") and low != "id":
        # identifiants stockes comme chaines : evite les references croisees vers
        # une table qui n'existe pas dans ce schema.
        return "v.string()"
    if any(low.startswith(h) for h in NUMERIC_HINTS):
        return "v.number()"
    if any(low.startswith(h) for h in BOOL_HINTS):
        return "v.boolean()"
    return "v.string()"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def app_schema_ts(entities: list[tuple[str, str]]) -> str:
    blocks = []
    for table, spec in entities:
        fields = _fields(spec)
        lines = [f"  {_safe(table)}: defineTable({{"]
        for field in fields:
            lines.append(f"    {_safe(field)}: {ts_validator(field)},")
        if table != "users" and "ownerId" not in fields:
            lines.append("    ownerId: v.string(),")
        lines.append("  })")
        indexes = []
        if table != "users":
            indexes.append('    .index("by_owner", ["ownerId"])')
        if "email" in fields:
            indexes.append('    .index("by_email", ["email"])')
        if indexes:
            lines[-1] = lines[-1] + "\n" + "\n".join(indexes)
        blocks.append("\n".join(lines))
    body = ",\n".join(blocks)
    return (
        'import { defineSchema, defineTable } from "convex/server";\n'
        'import { v } from "convex/values";\n\n'
        "export default defineSchema({\n"
        f"{body},\n"
        "});\n"
    )


def app_functions_ts(table: str, spec: str, auth_module: str = "./auth") -> str:
    """Queries et mutations gardees pour une table applicative."""
    fields = _fields(spec)
    owner_field = "ownerId" if table != "users" else None
    args = []
    for field in fields:
        if field in ("email",) and table == "users":
            continue
        if field == "ownerId":
            continue  # toujours derive de l'identite, jamais du client
        args.append(f"    {_safe(field)}: {ts_validator(field)},")
    args_block = "\n".join(args)

    guard = (
        "    const identity = await ctx.auth.getUserIdentity();\n"
        '    if (!identity) throw new Error("Non authentifie");\n'
    )
    owner = '    const ownerId = identity.subject;\n'

    if table == "users":
        return (
            'import { query, mutation } from "./_generated/server";\n'
            'import { v } from "convex/values";\n\n'
            "export const me = query({\n"
            "  args: {},\n"
            "  handler: async (ctx) => {\n"
            f"{guard}"
            "    return await ctx.db\n"
            '      .query("users")\n'
            '      .withIndex("by_email", (q) => q.eq("email", identity.email ?? ""))\n'
            "      .unique();\n"
            "  },\n"
            "});\n\n"
            "export const ensureProfile = mutation({\n"
            "  args: {},\n"
            "  handler: async (ctx) => {\n"
            f"{guard}"
            "    const existing = await ctx.db\n"
            '      .query("users")\n'
            '      .withIndex("by_email", (q) => q.eq("email", identity.email ?? ""))\n'
            "      .unique();\n"
            "    if (existing) return existing._id;\n"
            "    return await ctx.db.insert(\"users\", {\n"
            '      email: identity.email ?? "",\n'
            '      displayName: identity.name ?? "",\n'
            '      role: "user",\n'
            "    });\n"
            "  },\n"
            "});\n"
        )

    return (
        'import { query, mutation } from "./_generated/server";\n'
        'import { v } from "convex/values";\n\n'
        "export const list = query({\n"
        "  args: {},\n"
        "  handler: async (ctx) => {\n"
        f"{guard}"
        "    return await ctx.db\n"
        f'      .query("{_safe(table)}")\n'
        f'      .withIndex("by_owner", (q) => q.eq("ownerId", identity.subject))\n'
        "      .order(\"desc\")\n"
        "      .take(100);\n"
        "  },\n"
        "});\n\n"
        "export const get = query({\n"
        "  args: { id: v.id(\"" + _safe(table) + "\") },\n"
        "  handler: async (ctx, args) => {\n"
        f"{guard}"
        f'    const doc = await ctx.db.get(args.id);\n'
        f'    if (!doc || doc.ownerId !== identity.subject) throw new Error("Acces refuse");\n'
        "    return doc;\n"
        "  },\n"
        "});\n\n"
        "export const create = mutation({\n"
        f"  args: {{\n{args_block}\n  }},\n"
        "  handler: async (ctx, args) => {\n"
        f"{guard}{owner}"
        f'    return await ctx.db.insert("{_safe(table)}", {{ ...args, ownerId }});\n'
        "  },\n"
        "});\n\n"
        "export const update = mutation({\n"
        "  args: { id: v.id(\"" + _safe(table) + "\"), patch: v.object({}) },\n"
        "  handler: async (ctx, args) => {\n"
        f"{guard}"
        f'    const doc = await ctx.db.get(args.id);\n'
        f'    if (!doc || doc.ownerId !== identity.subject) throw new Error("Acces refuse");\n'
        "    await ctx.db.patch(args.id, args.patch);\n"
        "    return args.id;\n"
        "  },\n"
        "});\n"
    )


def auth_config_ts() -> str:
    return (
        "/** Configuration Convex Auth (providers). */\n"
        "export default {\n"
        "  providers: [\n"
        "    {\n"
        "      domain: process.env.CONVEX_SITE_URL,\n"
        "      applicationID: \"convex\",\n"
        "    },\n"
        "  ],\n"
        "};\n"
    )


# --- Niveau 1 : plateforme HakBuild (PRD section 11) -------------------------

PLATFORM_TABLES: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = [
    ("users", ["email", "name", "plan", "credits"], [("by_email", ["email"])]),
    ("projects", ["ownerId", "name", "idea", "platforms", "status", "repoUrl", "convexProject"],
     [("by_owner", ["ownerId"]), ("by_status", ["status"])]),
    ("conversations", ["projectId", "title"], [("by_project", ["projectId"])]),
    ("messages", ["conversationId", "projectId", "role", "content"], [("by_project", ["projectId"])]),
    ("requirements", ["projectId", "version", "content", "status"], [("by_project", ["projectId"])]),
    ("jobs", ["projectId", "kind", "status"], [("by_project", ["projectId"]), ("by_status", ["status"])]),
    ("tasks", ["projectId", "jobId", "title", "phase", "status", "attempts"], [("by_project", ["projectId"])]),
    ("agentRuns", ["projectId", "taskId", "command", "exitCode", "output"], [("by_task", ["taskId"])]),
    ("logs", ["projectId", "level", "message"], [("by_project", ["projectId"])]),
    ("builds", ["projectId", "platform", "profile", "status", "artifactPath"],
     [("by_project", ["projectId"])]),
    ("deployments", ["projectId", "kind", "status", "target"],
     [("by_project", ["projectId"])]),
    ("artifacts", ["projectId", "kind", "path", "size", "sha256"], [("by_project", ["projectId"])]),
    ("repositories", ["projectId", "provider", "url", "visibility"], [("by_project", ["projectId"])]),
    ("integrations", ["projectId", "name", "status", "configJson"], [("by_project", ["projectId"])]),
    ("testResults", ["projectId", "suite", "passed", "failed"], [("by_project", ["projectId"])]),
    ("securityReports", ["projectId", "severity", "findingsJson"], [("by_project", ["projectId"])]),
    ("approvals", ["projectId", "gate", "status", "decidedBy"],
     [("by_project", ["projectId"]), ("by_status", ["status"])]),
    ("subscriptions", ["userId", "plan", "status", "currentPeriodEnd"], [("by_user", ["userId"])]),
    ("creditTransactions", ["userId", "delta", "reason"], [("by_user", ["userId"])]),
    ("notifications", ["userId", "projectId", "title", "readAt"], [("by_user", ["userId"])]),
    ("auditLogs", ["actor", "action", "target", "meta"], [("by_actor", ["actor"])]),
]


def platform_schema_ts() -> str:
    blocks = []
    for table, fields, indexes in PLATFORM_TABLES:
        lines = [f"  {table}: defineTable({{"]
        for field in fields:
            lines.append(f"    {field}: {ts_validator(field)},")
        lines.append("  })")
        for index_name, index_fields in indexes:
            lines[-1] += f'\n    .index("{index_name}", {_ts_array(index_fields)})'
        blocks.append("\n".join(lines))
    body = ",\n".join(blocks)
    return (
        "/** Convex schema de la plateforme HakBuild (niveau 1). */\n"
        'import { defineSchema, defineTable } from "convex/server";\n'
        'import { v } from "convex/values";\n\n'
        "export default defineSchema({\n"
        f"{body},\n"
        "});\n"
    )


def _ts_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def schema_tables(schema_ts: str) -> list[str]:
    """Liste les tables declarees dans un schema TS genere (verification)."""
    return re.findall(r"^\s{2}([A-Za-z0-9_]+): defineTable\(", schema_ts, re.M)
