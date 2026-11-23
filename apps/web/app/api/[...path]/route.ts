import { NextRequest, NextResponse } from "next/server";

const upstream = process.env.MERCURY_API_URL || "http://127.0.0.1:8040";
const cookieName = "mercury_session";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
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
  if (path.join("/") === "auth/logout") {
    const response = NextResponse.json({ ok: true });
    response.cookies.delete(cookieName);
    return response;
  }
  const token = request.cookies.get(cookieName)?.value;
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  try {
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
      response.cookies.set(cookieName, data.access_token, {
        httpOnly: true,
        sameSite: "strict",
        secure: process.env.MERCURY_COOKIE_SECURE === "true",
        path: "/",
        maxAge: data.expires_in,
      });
      return response;
    }
    const responseHeaders = new Headers({
      "Content-Type": result.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    const disposition = result.headers.get("content-disposition");
    if (disposition) responseHeaders.set("Content-Disposition", disposition);
    return new NextResponse(await result.arrayBuffer(), {
      status: result.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      { error: { message: "服务连接失败，请确认 API 已启动" } },
      { status: 502 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH };
