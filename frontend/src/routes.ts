import type { Page } from "./types";

const routeByPage: Record<Page, string> = {
  chat: "/",
  archives: "/archives",
  memories: "/memories",
  settings: "/settings",
  "model-logs": "/model-logs",
};

const pageByRoute = new Map(Object.entries(routeByPage).map(([page, path]) => [path, page as Page]));

function normalizedPathname(pathname: string) {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed || "/";
}

export function pageFromPathname(pathname: string): Page {
  return pageByRoute.get(normalizedPathname(pathname)) || "chat";
}

export function pathForPage(page: Page) {
  return routeByPage[page];
}
