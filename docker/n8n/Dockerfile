FROM alpine:3.22 AS curl-builder
RUN apk add --no-cache curl

FROM n8nio/n8n:2.12.2

ENV GENERIC_TIMEZONE=Asia/Ho_Chi_Minh
ENV TZ=Asia/Ho_Chi_Minh

# Copy curl and its dependencies from Alpine
COPY --from=curl-builder /usr/bin/curl /usr/bin/curl
COPY --from=curl-builder /usr/lib/libcurl* /usr/lib/
COPY --from=curl-builder /usr/lib/libbrotli* /usr/lib/
COPY --from=curl-builder /usr/lib/libnghttp2* /usr/lib/
COPY --from=curl-builder /usr/lib/libidn2* /usr/lib/
COPY --from=curl-builder /usr/lib/libunistring* /usr/lib/
COPY --from=curl-builder /usr/lib/libpsl* /usr/lib/

# Install n8n-nodes-puppeteer community node
USER node
RUN mkdir -p /home/node/.n8n/nodes && \
    cd /home/node/.n8n/nodes && \
    npm init -y && \
    npm install n8n-nodes-puppeteer

# Copy scripts and workflows
USER root
COPY docker/n8n/fetch-page.js /home/node/fetch-page.js
COPY docker/n8n/import-workflows.sh /home/node/import-workflows.sh
RUN mkdir -p /home/node/workflows
COPY workflows/*.json /home/node/workflows/

# Open permissions so node user can read/write
RUN chmod 755 /home/node && \
    chmod -R 777 /home/node/.n8n && \
    chmod 755 /home/node/import-workflows.sh /home/node/fetch-page.js && \
    chmod -R 755 /home/node/workflows

EXPOSE 5678

ENTRYPOINT ["sh", "/home/node/import-workflows.sh"]
