FROM node:24-slim

WORKDIR /usr/src/app

# Copy the manifests first so the install layer caches independently of source changes.
COPY package.json package-lock.json ./

# npm ci, not npm install: installs exactly the committed lockfile and fails loudly on drift.
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
