// DEV-534 — screenshots in the conversation with the Staviteľ, as in Nápady: what the box takes, and one screenshot for
// its small preview at the message. Apart from the pipeline client: the box asks for the limits whatever the page mocks.
import type { AttachmentLimits } from "@/lib/poradcaAttachments";
import api, { fetchBlob } from "../api";

/** A screenshot as the conversation box sends it: its name and the content in base64 (as Poradca's). */
export interface ScreenshotUpload {
  name: string;
  data: string;
}

/** What the conversation box takes: Poradca's limits, images only. */
export function getScreenshotLimitsApi(versionId: string): Promise<AttachmentLimits> {
  return api.get<AttachmentLimits>(`/pipeline/${versionId}/screenshot-limits`);
}

/** One screenshot pinned to a message of the conversation. */
export function getScreenshotApi(versionId: string, imageId: string): Promise<Blob> {
  return fetchBlob(`/pipeline/${versionId}/screenshots/${imageId}`);
}
