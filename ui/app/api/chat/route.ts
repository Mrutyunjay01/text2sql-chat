// Server-side proxy to the FastAPI backend, so the browser only talks to this origin
// and the api container is never exposed. API_URL is read at runtime.
export async function POST(req: Request) {
  const apiUrl = process.env.API_URL ?? "http://localhost:8000";
  try {
    const upstream = await fetch(`${apiUrl}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await req.text(),
      signal: AbortSignal.timeout(120_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}
