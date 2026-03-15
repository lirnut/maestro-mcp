#!/bin/bash
# Build and run test environment for offline persistence testing

set -e

IMAGE_NAME="maestro-test-remote"
CONTAINER_NAME="maestro-test-remote"
SSH_PORT=2222

echo "Building test Docker image..."
docker build -t "$IMAGE_NAME" -f Dockerfile.test .

echo "Checking for existing container..."
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Removing existing container..."
    docker rm -f "$CONTAINER_NAME"
fi

echo "Starting test container..."
docker run -d \
    --name "$CONTAINER_NAME" \
    -p ${SSH_PORT}:22 \
    --hostname test-remote \
    "$IMAGE_NAME"

echo "Waiting for SSH to be ready..."
sleep 3

echo "Test environment ready!"
echo "  SSH: localhost:${SSH_PORT}"
echo "  User: testuser"
echo "  Password: testpass"
echo ""
echo "To connect: ssh -p ${SSH_PORT} testuser@localhost"
echo "To stop: docker stop ${CONTAINER_NAME}"