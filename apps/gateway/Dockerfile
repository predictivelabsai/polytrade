FROM node:20-alpine AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.base.json ./
COPY packages/contracts/package.json packages/contracts/tsconfig.json ./packages/contracts/
COPY apps/gateway/package.json apps/gateway/tsconfig.json apps/gateway/tsconfig.build.json ./apps/gateway/
RUN pnpm install --filter @polytrade/gateway... --frozen-lockfile
COPY packages/contracts ./packages/contracts
COPY apps/gateway ./apps/gateway
RUN pnpm --filter @polytrade/contracts build && pnpm --filter @polytrade/gateway build

FROM node:20-alpine
RUN corepack enable
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/packages/contracts ./packages/contracts
COPY --from=build /app/apps/gateway/package.json ./apps/gateway/package.json
COPY --from=build /app/apps/gateway/node_modules ./apps/gateway/node_modules
COPY --from=build /app/apps/gateway/dist ./apps/gateway/dist
COPY --from=build /app/apps/gateway/bootstrap ./apps/gateway/bootstrap
WORKDIR /app/apps/gateway
EXPOSE 4000
CMD ["sh", "-c", "node dist/bootstrap.js && exec node dist/index.js"]
