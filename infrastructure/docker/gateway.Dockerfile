# Official nginx distribution, with only the modules used by our HTTP/TLS proxy.
FROM registry.hub.docker.com/library/nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311
# apk removes actual modules and unreferenced libraries while retaining its database.
RUN apk del --no-network nginx-module-xslt nginx-module-njs nginx-module-image-filter \
        nginx-module-geoip nginx-module-acme \
    && apk add --no-cache 'zlib=1.4-r0' \
    && test ! -e /usr/lib/libxml2.so.2 \
    && test ! -e /usr/lib/libxslt.so.1
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
