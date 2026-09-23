FROM node:24-slim AS build
WORKDIR /app
COPY frontend/package*.json ./

# `npm ci` a secas se rinde al primer corte de red, y acá eso cuesta el build
# entero: con una conexion lenta la descarga tarda cerca de una hora, y un
# ECONNRESET a los 54 minutos tira todo ese trabajo (paso comprobado en la red
# de la FMP). Los valores por defecto de npm son 3 reintentos y 5 minutos de
# espera por peticion; se suben porque el problema no es que el registro no
# responda, es que responde lento y la conexion se corta cada tanto.
#
# --no-audit y --no-fund quitan dos peticiones que no aportan nada a una
# imagen de produccion y que tambien pueden colgarse.
RUN npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 180000 \
    && npm config set fetch-timeout 900000 \
    && npm ci --no-audit --no-fund

COPY frontend/ .
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
