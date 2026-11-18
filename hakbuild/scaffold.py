"""Scaffold de l'application mobile generee (React Native + Expo + TypeScript).

Les fichiers sont ecrits reellement dans le workspace du projet. Le rendu reste
un squelette fonctionnel : l'agent y ajoute ensuite les ecrans metier.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template

from . import convex as convex_mod
from . import prd as prd_mod
from .build import eas_json as _eas_json

THEME = """export const theme = {
  colors: {
    background: "#0B0F14",
    surface: "#141A22",
    primary: "#3B82F6",
    primaryDark: "#1D4ED8",
    text: "#F8FAFC",
    muted: "#94A3B8",
    border: "#1F2937",
    danger: "#EF4444",
    success: "#22C55E",
  },
  radius: { sm: 8, md: 14, lg: 22 },
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 },
  font: { body: 15, title: 22, hero: 30 },
};

export type Theme = typeof theme;
"""

SCREENS = """# Ecrans - $name

## Identite

- Fond `#0B0F14`, surface `#141A22`, accent `#3B82F6`.
- Rayon 14 px pour les cartes, 22 px pour les feuilles.
- Titre 30 px, titre de section 22 px, corps 15 px.

## 1. Accueil / liste (`app/index.tsx`)

- En-tete : nom de l'application, bouton d'ajout.
- Liste des elements de l'utilisateur connecte.
- Etat vide : "Aucun element pour le moment" + bouton de creation.
- Etat chargement : indicateur d'activite centre.
- Etat erreur : message + bouton "Reessayer".
- Etat succes : element cree apparait en tete de liste.

## 2. Creation (`app/create.tsx`)

- Formulaire : $fields.
- Validation cote client (champ obligatoire) et cote serveur (Convex).
- Bouton desactive pendant l'envoi, message d'erreur explicite.

## 3. Detail (`app/details/[id].tsx`)

- Lecture d'un element par identifiant.
- Acces refuse si l'element appartient a un autre utilisateur.
- Actions : modifier, retour.

## 4. Profil (`app/profile.tsx`)

- Email et nom issus de la session Convex.
- Bouton de deconnexion.

## Navigation

Expo Router, pile racine dans `app/_layout.tsx` : `index` -> `create` -> `details/[id]`.
"""

LAYOUT = """import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ConvexProvider } from "convex/react";

import { convex } from "../lib/convex";
import { theme } from "../constants/theme";

export default function RootLayout() {
  const navigation = (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.surface },
        headerTintColor: theme.colors.text,
        contentStyle: { backgroundColor: theme.colors.background },
      }}
    >
      <Stack.Screen name="index" options={{ title: "$name" }} />
      <Stack.Screen name="create" options={{ title: "Nouvel élément" }} />
      <Stack.Screen name="details/[id]" options={{ title: "Détail" }} />
      <Stack.Screen name="profile" options={{ title: "Profil" }} />
    </Stack>
  );

  return (
    <>
      <StatusBar style="light" />
      {convex ? <ConvexProvider client={convex}>{navigation}</ConvexProvider> : navigation}
    </>
  );
}
"""

INDEX = """import { Link } from "expo-router";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import { useList, usingDemo } from "../lib/backend";
import { theme } from "../constants/theme";

export default function HomeScreen() {
  const items = useList();

  if (items === undefined) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.colors.primary} />
        <Text style={styles.muted}>Chargement…</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {usingDemo ? (
        <Text style={styles.banner}>
          Mode démo : données locales. Définir EXPO_PUBLIC_CONVEX_URL pour activer le backend.
        </Text>
      ) : null}
      {items.length === 0 ? (
        <View style={styles.center}>
          <Text style={styles.title}>Aucun élément pour le moment</Text>
          <Text style={styles.muted}>Commencez par créer votre premier $label.</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item._id}
          contentContainerStyle={{ gap: theme.spacing.sm, padding: theme.spacing.md }}
          renderItem={({ item }) => (
            <Link href={{ pathname: "/details/[id]", params: { id: item._id } }} asChild>
              <Pressable style={styles.card}>
                <Text style={styles.cardTitle}>{String(item.$titleField ?? item._id)}</Text>
                <Text style={styles.muted}>{new Date(item._creationTime).toLocaleDateString()}</Text>
              </Pressable>
            </Link>
          )}
        />
      )}
      <Link href="/create" asChild>
        <Pressable style={styles.fab}>
          <Text style={styles.fabText}>+</Text>
        </Pressable>
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: theme.spacing.sm, padding: theme.spacing.lg },
  title: { color: theme.colors.text, fontSize: theme.font.title, fontWeight: "700", textAlign: "center" },
  muted: { color: theme.colors.muted, fontSize: theme.font.body, textAlign: "center" },
  card: {
    backgroundColor: theme.colors.surface,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.md,
    gap: 4,
  },
  cardTitle: { color: theme.colors.text, fontSize: theme.font.body, fontWeight: "600" },
  banner: {
    color: theme.colors.muted,
    backgroundColor: theme.colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
    fontSize: 12,
    paddingHorizontal: theme.spacing.md,
    paddingVertical: theme.spacing.sm,
    textAlign: "center",
  },
  fab: {
    position: "absolute",
    right: theme.spacing.lg,
    bottom: theme.spacing.xl,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: theme.colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  fabText: { color: "#FFFFFF", fontSize: 28, lineHeight: 32 },
});
"""

CREATE = """import { router } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { useCreate } from "../lib/backend";
import { theme } from "../constants/theme";

type Field = { key: string; label: string; numeric?: boolean; required?: boolean };

/** Champs generes depuis le schema Convex de l'application. */
const FIELDS: Field[] = [
$fields
];

export default function CreateScreen() {
  const createItem = useCreate();
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const payload: Record<string, unknown> = {};
    for (const field of FIELDS) {
      const raw = (form[field.key] ?? "").trim();
      if (field.required && !raw) {
        setError("Le champ « " + field.label + " » est obligatoire.");
        return;
      }
      if (field.numeric && raw && Number.isNaN(Number(raw))) {
        setError("« " + field.label + " » doit etre un nombre.");
        return;
      }
      payload[field.key] = field.numeric ? Number(raw || "0") : raw;
    }
    setSaving(true);
    setError(null);
    try {
      await createItem(payload);
      router.back();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Echec de l'enregistrement.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.container}>
      {FIELDS.map((field) => (
        <View key={field.key}>
          <Text style={styles.label}>
            {field.label}
            {field.required ? " *" : ""}
          </Text>
          <TextInput
            style={styles.input}
            value={form[field.key] ?? ""}
            onChangeText={(text) => setForm((prev) => ({ ...prev, [field.key]: text }))}
            placeholder={field.label}
            placeholderTextColor={theme.colors.muted}
            keyboardType={field.numeric ? "numeric" : "default"}
          />
        </View>
      ))}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.button} onPress={submit} disabled={saving}>
        {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Enregistrer</Text>}
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  container: { padding: theme.spacing.lg, gap: theme.spacing.md },
  label: { color: theme.colors.text, fontSize: theme.font.body, fontWeight: "600", marginBottom: 6 },
  input: {
    backgroundColor: theme.colors.surface,
    borderColor: theme.colors.border,
    borderWidth: 1,
    borderRadius: theme.radius.md,
    color: theme.colors.text,
    padding: theme.spacing.md,
    fontSize: theme.font.body,
  },
  error: { color: theme.colors.danger, fontSize: theme.font.body },
  button: {
    backgroundColor: theme.colors.primary,
    borderRadius: theme.radius.md,
    padding: theme.spacing.md,
    alignItems: "center",
  },
  buttonText: { color: "#FFFFFF", fontWeight: "700", fontSize: theme.font.body },
});
"""

DETAIL = """import { useLocalSearchParams } from "expo-router";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { useOne } from "../../lib/backend";
import { theme } from "../../constants/theme";

/** Libelles lisibles des champs du schema. */
const LABELS: Record<string, string> = {
$labels
};

export default function DetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const item = useOne(id);

  if (item === undefined) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>{String(item.$titleField ?? "Detail")}</Text>
      {Object.entries(item)
        .filter(([key]) => !key.startsWith("_") && key !== "ownerId" && key !== "$titleField")
        .map(([key, value]) => (
          <View key={key} style={styles.row}>
            <Text style={styles.key}>{LABELS[key] ?? key}</Text>
            <Text style={styles.value}>{String(value) || "-"}</Text>
          </View>
        ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.colors.background, padding: theme.spacing.lg, gap: theme.spacing.sm },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.background },
  title: { color: theme.colors.text, fontSize: theme.font.title, fontWeight: "700", marginBottom: theme.spacing.md },
  row: { flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: theme.colors.border, paddingVertical: theme.spacing.sm },
  key: { color: theme.colors.muted, fontSize: theme.font.body },
  value: { color: theme.colors.text, fontSize: theme.font.body, maxWidth: "65%", textAlign: "right" },
});
"""

PROFILE = """import { StyleSheet, Text, View } from "react-native";

import { theme } from "../constants/theme";

export default function ProfileScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Profil</Text>
      <Text style={styles.muted}>
        Session geree par Convex Auth. Brancher le provider dans convex/auth.ts avant publication.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.colors.background, padding: theme.spacing.lg, gap: theme.spacing.sm },
  title: { color: theme.colors.text, fontSize: theme.font.title, fontWeight: "700" },
  muted: { color: theme.colors.muted, fontSize: theme.font.body },
});
"""

DEMO_STORE = """type Row = { _id: string; _creationTime: number; [key: string]: unknown };

let rows: Row[] = [];
const listeners = new Set<() => void>();

/**
 * Mode demo : le stockage local permet de tester l'application sans deployment
 * Convex. Les donnees ne survivent pas au redemarrage de l'application.
 */
export const demoStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): Row[] {
    return rows;
  },
  add(fields: Record<string, unknown>): string {
    const row: Row = {
      _id: `demo-${Date.now()}`,
      _creationTime: Date.now(),
      ownerId: "demo",
      ...fields,
    };
    rows = [row, ...rows];
    listeners.forEach((listener) => listener());
    return row._id;
  },
};
"""

BACKEND = """import { useMutation, useQuery } from "convex/react";
import { useSyncExternalStore } from "react";

import { api } from "./api";
import { convex } from "./convex";
import { demoStore } from "./demoStore";

/**
 * Sans deployment Convex (EXPO_PUBLIC_CONVEX_URL absent), l'application bascule
 * en mode demo : les ecrans restent utilisables avec un stockage local.
 * `usingDemo` est une constante de module, donc l'ordre des hooks ne change jamais.
 */
export const usingDemo = !convex;

export type Row = { _id: string; _creationTime: number; [key: string]: unknown };

export function useList(): Row[] | undefined {
  if (usingDemo) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useSyncExternalStore(demoStore.subscribe, demoStore.getSnapshot);
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useQuery(api.$table.list) as Row[] | undefined;
}

export function useOne(id: string | undefined): Row | undefined {
  if (usingDemo) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const rows = useSyncExternalStore(demoStore.subscribe, demoStore.getSnapshot);
    return rows.find((row) => row._id === id);
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useQuery(api.$table.get, id ? { id: id as never } : "skip") as Row | undefined;
}

export function useCreate() {
  if (usingDemo) {
    return async (fields: Record<string, unknown>) => demoStore.add(fields);
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const create = useMutation(api.$table.create);
  return (fields: Record<string, unknown>) => create(fields as never);
}
"""

LIB_CONVEX = """import { ConvexReactClient } from "convex/react";

const url = process.env.EXPO_PUBLIC_CONVEX_URL;

/**
 * Aucune cle privee ici : seul l'URL publique du deployment Convex est lue.
 * Les secrets restent dans les variables d'environnement du backend.
 */
export const convex = url ? new ConvexReactClient(url) : null;
export const convexConfigured = Boolean(url);
"""

GITIGNORE = """node_modules/
.expo/
dist/
web-build/
*.log
.env
.env.local
.DS_Store
/convex/_generated/
"""

LIB_API = """import { anyApi } from "convex/server";

/**
 * Reference de fonctions Convex.
 *
 * `anyApi` permet de bundler et de tester l'application avant qu'un deployment
 * Convex n'existe. Apres `npx convex dev`, remplacer par :
 *   import { api } from "../convex/_generated/api";
 * pour obtenir le typage complet des fonctions.
 */
export const api = anyApi;
"""

ENV_EXAMPLE = """# URL publique du deployment Convex (pas un secret)
EXPO_PUBLIC_CONVEX_URL=https://<deployment>.convex.cloud
# Secrets backend : a definir avec `npx convex env set` (jamais dans ce fichier)
"""


FIELD_LABELS = {
    "customerName": "Client",
    "patientName": "Patient",
    "doctorName": "Médecin",
    "scheduledAt": "Rendez-vous le",
    "date": "Date",
    "slot": "Créneau",
    "status": "Statut",
    "title": "Titre",
    "name": "Nom",
    "label": "Libellé",
    "subject": "Objet",
    "description": "Description",
    "notes": "Notes",
    "amount": "Montant",
    "price": "Prix",
    "total": "Total",
    "currency": "Devise",
    "provider": "Moyen de paiement",
    "reference": "Référence",
    "stock": "Stock",
    "quantity": "Quantité",
    "body": "Message",
    "threadId": "Conversation",
    "authorId": "Auteur",
    "readAt": "Lu le",
    "items": "Articles",
    "address": "Adresse",
    "city": "Ville",
    "imageUrl": "Image (URL)",
    "email": "Email",
    "role": "Rôle",
    "displayName": "Nom affiché",
    "class": "Classe",
    "grades": "Notes",
}


def create_fields(fields: list[str]) -> str:
    lines = []
    for field in fields:
        if field == "ownerId":
            continue
        numeric = convex_mod.ts_validator(field) == "v.number()"
        label = FIELD_LABELS.get(field, field)
        required = field != "status" and not field.endswith("Id")
        lines.append(
            f'  {{ key: "{field}", label: "{label}", numeric: '
            f'{"true" if numeric else "false"}, required: {"true" if required else "false"} }},'
        )
    return "\n".join(lines)


def field_labels_block(fields: list[str]) -> str:
    return "\n".join(
        f'  {field}: "{FIELD_LABELS.get(field, field)}",' for field in fields if field != "ownerId"
    )


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "app").lower()).strip("-")
    return slug or "app"


def _pascal(name: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", name) if p) or "Item"


def main_entity(project: dict) -> tuple[str, str]:
    entities = prd_mod.entities_for(project.get("idea") or "")
    candidates = [e for e in entities if e[0] != "users"]
    return candidates[0] if candidates else ("items", "title, description, ownerId")


def app_files(project: dict) -> dict[str, str]:
    name = project.get("name") or "Application"
    slug = _slug(name)
    table, spec = main_entity(project)
    fields = [f.strip() for f in spec.split(",") if f.strip()]
    title_field = next(
        (f for f in fields if f in ("title", "name", "label", "subject", "customerName", "patientName", "email")),
        fields[0],
    )
    label = table.rstrip("s")
    entities = prd_mod.entities_for(project.get("idea") or "")

    files: dict[str, str] = {}
    files["design/screens.md"] = Template(SCREENS).substitute(
        name=name,
        fields=", ".join(fields),
    )
    files["constants/theme.ts"] = THEME
    files["package.json"] = json.dumps(
        {
            "name": slug,
            "version": "1.0.0",
            "main": "expo-router/entry",
            "scripts": {
                "start": "expo start",
                "android": "expo start --android",
                "ios": "expo start --ios",
                "web": "expo start --web",
                "tsc": "tsc --noEmit",
            },
            "dependencies": {
                "@convex-dev/auth": "^0.0.80",
                "convex": "^1.17.0",
                "expo": "~54.0.0",
                "expo-router": "~6.0.0",
                "expo-status-bar": "~3.0.0",
                "react": "19.1.0",
                "react-dom": "19.1.0",
                "react-native": "0.81.4",
                "react-native-safe-area-context": "~5.6.0",
                "react-native-screens": "~4.16.0",
                "react-native-web": "~0.21.0",
            },
            "devDependencies": {
                "@types/react": "~19.1.0",
                "typescript": "~5.9.2",
            },
            "private": True,
        },
        indent=2,
    ) + "\n"
    files["app.json"] = json.dumps(
        {
            "expo": {
                "name": name,
                "slug": slug,
                "version": "1.0.0",
                "orientation": "portrait",
                "scheme": slug,
                "userInterfaceStyle": "dark",
                "newArchEnabled": True,
                "splash": {"backgroundColor": "#0B0F14", "resizeMode": "contain"},
                "ios": {"supportsTablet": True, "bundleIdentifier": f"com.hakbuild.{slug.replace('-', '')}",
                        "buildNumber": "1"},
                "android": {
                    "package": f"com.hakbuild.{slug.replace('-', '')}",
                    "versionCode": 1,
                    "edgeToEdgeEnabled": True,
                    "permissions": ["INTERNET"],
                },
                "web": {"bundler": "metro", "output": "static"},
                "plugins": ["expo-router"],
                "experiments": {"typedRoutes": True},
            }
        },
        indent=2,
    ) + "\n"
    files["tsconfig.json"] = json.dumps(
        {
            "extends": "expo/tsconfig.base",
            "compilerOptions": {
                "strict": True,
                "baseUrl": ".",
                "paths": {"@/*": ["./*"]},
            },
            "include": ["**/*.ts", "**/*.tsx", ".expo/types/**/*.ts", "expo-env.d.ts"],
        },
        indent=2,
    ) + "\n"
    files["app/_layout.tsx"] = Template(LAYOUT).substitute(name=name)
    files["app/index.tsx"] = Template(INDEX).substitute(
        table=table, label=label, titleField=title_field
    )
    files["app/create.tsx"] = Template(CREATE).substitute(fields=create_fields(fields))
    files["app/details/[id].tsx"] = Template(DETAIL).substitute(
        table=table, titleField=title_field, labels=field_labels_block(fields)
    )
    files["app/profile.tsx"] = PROFILE
    files["lib/convex.ts"] = LIB_CONVEX
    files["lib/demoStore.ts"] = DEMO_STORE
    files["lib/backend.ts"] = Template(BACKEND).substitute(table=table)
    files[".env.example"] = ENV_EXAMPLE
    files[".gitignore"] = GITIGNORE
    files[".easignore"] = (
        "node_modules/\n"
        "dist/\n"
        ".expo/\n"
        ".git/\n"
        "*.log\n"
        ".env\n"
    )
    files["lib/api.ts"] = LIB_API
    files["eas.json"] = _eas_json()
    files["babel.config.js"] = (
        "module.exports = function (api) {\n"
        "  api.cache(true);\n"
        '  return { presets: ["babel-preset-expo"] };\n'
        "};\n"
    )
    files["expo-env.d.ts"] = '/// <reference types="expo/types" />\n'
    files["convex/schema.ts"] = convex_mod.app_schema_ts(entities)
    files["convex/auth.config.ts"] = convex_mod.auth_config_ts()
    for table_name, spec_fields in entities:
        files[f"convex/{table_name}.ts"] = convex_mod.app_functions_ts(table_name, spec_fields)
    files["README.md"] = _readme(name, table, fields)
    return files


def _readme(name: str, table: str, fields: list[str]) -> str:
    return f"""# {name}

Application generee par HakBuild AI.

## Demarrage

```bash
npm install
cp .env.example .env      # renseigner EXPO_PUBLIC_CONVEX_URL
npx convex dev            # cree le deployment et genere convex/_generated
npx expo start
```

## Structure

- `app/` : ecrans Expo Router (liste, creation, detail, profil).
- `convex/schema.ts` : tables `{table}` et index.
- `convex/{table}.ts` : queries et mutations gardees par identite.
- `lib/convex.ts` : client Convex, aucune cle privee.

## Champs de `{table}`

{', '.join(fields)}

## Build

```bash
npx eas-cli build --platform android --profile preview
```
"""
