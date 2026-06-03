import { describe, expect, it } from "vitest";

import { buildPipelineWsUrl } from "@/services/api/pipeline";

describe("buildPipelineWsUrl", () => {
  const versionId = "11111111-1111-1111-1111-111111111111";

  it("targets the pipeline WS path with an encoded token query", () => {
    const url = buildPipelineWsUrl(versionId, "tok en/+=");
    expect(url).toContain(`/api/v1/pipeline/ws/${versionId}?token=`);
    expect(url).toContain(encodeURIComponent("tok en/+="));
  });

  it("uses a ws/wss scheme, never http", () => {
    const url = buildPipelineWsUrl(versionId, "t");
    expect(url.startsWith("ws:") || url.startsWith("wss:")).toBe(true);
    expect(url).not.toContain("http");
  });
});
