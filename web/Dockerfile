FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 9999
CMD ["sh", "-c", "npm run start -- --hostname 0.0.0.0 --port 9999"]
