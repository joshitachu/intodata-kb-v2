# TODO: Frontend Dockerfile (Next.js standalone)
FROM node:18-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG KB_API_URL=http://mcp-server:8002
ENV KB_API_URL=$KB_API_URL
ARG NEXT_PUBLIC_TENANT_NAME=Nexus
ARG NEXT_PUBLIC_TENANT_SLUG=nexus
ENV NEXT_PUBLIC_TENANT_NAME=$NEXT_PUBLIC_TENANT_NAME
ENV NEXT_PUBLIC_TENANT_SLUG=$NEXT_PUBLIC_TENANT_SLUG
RUN npm run build

FROM node:18-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
