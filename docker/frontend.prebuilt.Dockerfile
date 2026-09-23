# Imagen del frontend a partir de un `dist` ya compilado.
#
# POR QUÉ EXISTE
# --------------
# `docker/frontend.Dockerfile` compila dentro del contenedor, que es lo
# correcto: la imagen sale igual en cualquier máquina y no depende de lo que
# haya instalado en la de turno. Ese es el que se usa en el servidor.
#
# Pero `npm ci` necesita bajar 146 paquetes (133 MB) del registro de npm, y en
# la red de la FMP eso no termina: dos intentos murieron con ECONNRESET a los
# 54 y a los 65 minutos, con la descarga a medias. Sin una imagen, cada vez
# que alguien recrea el contenedor `web` se pierde el frontend desplegado.
#
# Este Dockerfile arma la misma imagen final -- nginx sirviendo `dist` con la
# misma configuración -- tomando el `dist` que ya se compiló afuera. El
# resultado que sirve al navegador es el mismo: Vite produce JavaScript y CSS,
# que no dependen del sistema donde se compilaron.
#
# CÓMO SE USA
# -----------
#   cd frontend && npm ci && npm run build      # produce frontend/dist
#   cd ..
#   docker build -f docker/frontend.prebuilt.Dockerfile -t sistema_ctot-web:latest .
#   docker compose up -d --no-build web
#
# CUÁNDO NO USARLO
# ----------------
# En el servidor, y en cualquier despliegue formal, se usa
# `docker/frontend.Dockerfile`. Este es para una máquina que ya tiene el
# `node_modules` instalado y una red que no permite reinstalarlo.
#
# La comprobación de que `dist` existe está a propósito: sin ella, un `dist`
# ausente produce una imagen que arranca bien y sirve un 404 en blanco, y el
# error recién aparece en el navegador de un operador.

FROM nginx:alpine

COPY frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

RUN test -f /usr/share/nginx/html/index.html \
    || (echo "FALTA frontend/dist: compilalo con 'npm run build' antes de armar esta imagen" && exit 1)

EXPOSE 80
