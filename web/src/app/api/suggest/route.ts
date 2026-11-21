/**
 * Legacy alias — forwards multipart/JSON bodies to the async job starter.
 * Prefer POST /api/jobs/suggest and poll GET /api/jobs/:id.
 */
export { POST } from "@/app/api/jobs/suggest/route"
