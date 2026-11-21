# Development-stage image for the Next.js frontend.
# A production multi-stage build is added in Phase 15.
FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
