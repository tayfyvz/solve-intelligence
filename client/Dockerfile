# Two targets from one file. `docker-compose.yml` asks for `dev`; the default target is
# the LAST stage, so a plain `docker build ./client` produces the production image.
# Same principle as the server: dev and prod differ by what compose selects, never by a
# branch inside the application.

# ---- dev: the Vite dev server. Source maps, HMR, an unminified bundle, and a server
#      that was never written to face the internet. Correct for compose, wrong for
#      production, and it says so here rather than being discovered in staging.
FROM node:24-slim AS dev
WORKDIR /usr/src/app

# Copy the manifests first so the install layer caches independently of source changes.
COPY package.json package-lock.json ./

# npm ci, not npm install: installs exactly the committed lockfile and fails loudly on drift.
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]


# ---- build: the real bundle. Reuses `dev` rather than repeating `npm ci`, so the
#      production image is built from the byte-identical dependency tree the dev image
#      installed.
FROM dev AS build

# THE GOTCHA, stated where it bites. Vite INLINES every VITE_* variable at BUILD time, so
# the API origin is baked into the image and a container started with a different
# VITE_API_URL will ignore it — completely, silently, and still call the old host.
#
# The empty default is deliberate and is option (1) of PLAN §28.2.2: same-origin. api.ts
# reads `import.meta.env.VITE_API_URL ?? "http://localhost:8000"` and every path it
# requests already begins with `/api/`, so VITE_API_URL is an ORIGIN, not a path prefix —
# "" makes axios issue relative requests against whatever host served the page, and the
# nginx block below proxies /api to the server. Nothing environment-specific is inlined,
# so one artefact is promotable to every environment, and the CORS middleware becomes a
# no-op for the browser path.
#
# (PLAN §28.2.2 wrote this default as `/api`. That is wrong for THIS client: baseURL
# "/api" plus path "/api/ai/chat" requests "/api/api/ai/chat". Correction 38, §27.4.)
#
# The two alternatives, named so the reader knows they were considered and rejected:
#   (2) one image per environment via --build-arg. Correct, but the artefact you tested
#       is then not the artefact you promote.
#   (3) a runtime /config.json the app fetches before its first API call. Most flexible;
#       costs a round-trip and a loading state.
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build


# ---- production: static files behind nginx. No node, no source, no source maps.
FROM nginx:alpine AS production
COPY --from=build /usr/src/app/dist /usr/share/nginx/html
# SPA fallback, the /api reverse proxy, and the body cap from §28.2.4 — one file.
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
