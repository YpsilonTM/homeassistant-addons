FROM oven/bun:1.0 AS builder

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install

COPY . .

# Use a clean base image
FROM ghcr.io/home-assistant/base:3.23

WORKDIR /app

# Install curl/unzip to install bun on the alpine base
RUN apk add --no-cache curl unzip python3
RUN curl -fsSL https://bun.sh/install | bash \
    && ln -s ~/.bun/bin/bun /usr/local/bin/bun

COPY --from=builder /app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
