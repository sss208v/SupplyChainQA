# ---- Build stage ----
FROM node:18-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json ./
RUN npm install

# Copy source
COPY . .

# Build (VITE_API_URL injected at build time)
ARG VITE_API_URL=http://localhost:8001
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# ---- Runtime stage ----
FROM nginx:alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
