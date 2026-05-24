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
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]
