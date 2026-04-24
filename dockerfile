FROM node:18-slim

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm install --production

# Copy the rest of the frontend code
# Note: .dockerignore will handle excluding backend/ and node_modules/
COPY . .

# Set environment variables
ENV PORT=8080
EXPOSE 8080

# Start the Node.js server
CMD ["npm", "start"]
