# Build context is the repository root (needs the pnpm workspace files):
#   docker build -f apps/web/Dockerfile .
FROM node:26-alpine AS build

WORKDIR /repo
RUN npm install --global pnpm@10.18.1

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile

COPY apps/web apps/web
RUN pnpm --filter web run build
RUN find /repo/apps/web/dist/web/browser -type f \
    \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.json' \
       -o -name '*.webmanifest' \) \
    -exec gzip -9 -k '{}' \;

FROM nginx:1.29-alpine

COPY apps/web/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /repo/apps/web/dist/web/browser /usr/share/nginx/html

EXPOSE 80
