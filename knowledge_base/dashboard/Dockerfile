FROM node:18-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_LOGIN_URL=https://nexus.intodata.nl/login
ENV VITE_LOGIN_URL=$VITE_LOGIN_URL
# Fleet / Mission Control UI — only enabled on the nexus operator server.
# Customer VPS instances must NOT set this to true.
ARG VITE_FLEET_ENABLED=false
ENV VITE_FLEET_ENABLED=$VITE_FLEET_ENABLED
# Isolated OpenCode A/B workspace. Default-off independently from Fleet.
ARG VITE_AGENTIC_EXPERIMENTS_ENABLED=false
ENV VITE_AGENTIC_EXPERIMENTS_ENABLED=$VITE_AGENTIC_EXPERIMENTS_ENABLED
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx processes *.template files in /etc/nginx/templates/ at startup via envsubst.
# NGINX_ENVSUBST_TEMPLATE_VARS limits substitution to MC_API_KEY only, so nginx
# variables like $host and $remote_addr are left untouched.
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]
