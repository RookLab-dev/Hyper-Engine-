import { ApiError } from "@/types";

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? `${error.kind} (${error.status}): ${error.message}` : String(error);
  return <p className="error">{msg}</p>;
}