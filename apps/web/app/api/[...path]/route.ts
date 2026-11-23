import { NextRequest, NextResponse } from "next/server";

const upstream = process.env.MERCURY_API_URL || "http://127.0.0.1:8040";
const cookieName = "mercury_session";
const bindingCookie = "mercury_login_binding";
const secureCookie = process.env.MERCURY_COOKIE_SECURE === "true";

function saveSession(data: { access_token: string; expires_in: number }, response: NextResponse) {
  response.cookies.set(cookieName, data.access_token, {
    httpOnly: true, sameSite: "strict", secure: secureCookie, path: "/", maxAge: data.expires_in,
  });
  return response;
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = path.join("/");
  if (path.some((part) => !/^[a-zA-Z0-9._-]+$/.test(part) || part === ".."))
    return NextResponse.json(
      { error: { message: "路径无效" } },
      { status: 400 },
    );
  const mutating = !["GET", "HEAD"].includes(request.method);
  if (mutating) {
    const origin = request.headers.get("origin");
    const localHost = request.headers.get("host");
    const expectedOrigin =
      process.env.MERCURY_PUBLIC_ORIGIN ||
      (["localhost:3040", "127.0.0.1:3040"].includes(localHost || "")
        ? `http://${localHost}`
        : request.nextUrl.origin);
    if (origin !== expectedOrigin) {
      return NextResponse.json(
        { error: { message: "请求来源校验失败" } },
        { status: 403 },
      );
    }
    const length = Number(request.headers.get("content-length") || 0);
    if (length > 6 * 1024 * 1024)
      return NextResponse.json(
        { error: { message: "请求过大" } },
        { status: 413 },
      );
  }
  if (route.startsWith("auth/") && request.method === "GET" &&
      !["auth/config", "auth/oidc/start", "auth/oidc/callback"].includes(route))
    return NextResponse.json({ error: { message: "请求方法无效" } }, { status: 405 });
  const token = request.cookies.get(cookieName)?.value;
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  try {
    if (["auth/oidc/start", "auth/oidc/callback"].includes(route)) {
      if (request.method !== "GET")
        return NextResponse.json({ error: { message: "请求方法无效" } }, { status: 405 });
      const start = route.endsWith("start");
      const params = request.nextUrl.searchParams;
      const input = start
        ? { tenant_id: params.get("tenant") || "", store_id: params.get("store") || "" }
        : { state: params.get("state") || "", code: params.get("code") || "",
            binding: request.cookies.get(bindingCookie)?.value || "" };
      const result = await fetch(`${upstream}/api/${route}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
        redirect: "error", cache: "no-store", signal: AbortSignal.timeout(20000),
      });
      const data = await result.json();
      if (!result.ok) {
        const failure = NextResponse.redirect(new URL("/?login_error=1", process.env.MERCURY_PUBLIC_ORIGIN || request.nextUrl.origin));
        failure.cookies.delete(bindingCookie);
        return failure;
      }
      if (start) {
        const response = NextResponse.redirect(data.authorization_url);
        response.cookies.set(bindingCookie, data.binding, { httpOnly: true, sameSite: "lax",
          secure: secureCookie, path: "/api/auth/oidc", maxAge: 300 });
        return response;
      }
      const response = saveSession(data, NextResponse.redirect(new URL("/", process.env.MERCURY_PUBLIC_ORIGIN || request.nextUrl.origin)));
      response.cookies.set(bindingCookie, "", { path: "/api/auth/oidc", maxAge: 0 });
      return response;
    }
    const body = mutating ? await request.arrayBuffer() : undefined;
    if (body && body.byteLength > 6 * 1024 * 1024)
      return NextResponse.json(
        { error: { message: "请求过大" } },
        { status: 413 },
      );
    const result = await fetch(
      `${upstream}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`,
      {
        method: request.method,
        headers,
        body,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(60000),
      },
    );
    if (path.join("/") === "auth/login" && result.ok) {
      const data = await result.json();
      const response = NextResponse.json({ ok: true });
      return saveSession(data, response);
    }
    if (route === "auth/logout" && result.ok) {
      const response = NextResponse.json({ ok: true });
      response.cookies.delete(cookieName);
      return response;
    }
    const responseHeaders = new Headers({
      "Content-Type": result.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    const disposition = result.headers.get("content-disposition");
    if (disposition) responseHeaders.set("Content-Disposition", disposition);
    if (route === "events" && result.ok) {
      responseHeaders.set("X-Accel-Buffering", "no");
      return new NextResponse(result.body, { status: result.status, headers: responseHeaders });
    }
    const response = new NextResponse(await result.arrayBuffer(), {
      status: result.status,
      headers: responseHeaders,
    });
    if (result.status === 401) response.cookies.delete(cookieName);
    return response;
  } catch {
    return NextResponse.json(
      { error: { message: "服务连接失败，请确认 API 已启动" } },
      { status: 502 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH };
