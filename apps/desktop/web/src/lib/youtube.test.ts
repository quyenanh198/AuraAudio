import { describe, expect, it } from "vitest";

import { defaultYoutubeTitle, isYoutubeUrl } from "./youtube";

describe("isYoutubeUrl", () => {
  const VALID_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
  ];

  it.each(VALID_URLS)("accepts %s", (url) => {
    expect(isYoutubeUrl(url)).toBe(true);
  });

  const INVALID_URLS: [string, string][] = [
    ["https://youtube.com@evil.com/watch?v=x", "userinfo trick -- real host is evil.com"],
    ["https://evil.com/watch?v=x", "non-YouTube host"],
    ["https://youtube.com.evil.com/watch?v=x", "suffix trick"],
    ["https://evil.com/youtube.com/watch?v=x", "youtube.com only in the path"],
    ["ftp://youtube.com/watch?v=x", "disallowed scheme"],
    ["javascript:alert(1)", "disallowed scheme, no host"],
    ["", "empty string"],
    ["not a url", "not a URL at all"],
  ];

  it.each(INVALID_URLS)("rejects %s (%s)", (url) => {
    expect(isYoutubeUrl(url)).toBe(false);
  });
});

describe("defaultYoutubeTitle", () => {
  it("extracts the video id from a watch URL", () => {
    expect(defaultYoutubeTitle("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toBe(
      "YouTube import (dQw4w9WgXcQ)",
    );
  });

  it("extracts the video id from a youtu.be short URL", () => {
    expect(defaultYoutubeTitle("https://youtu.be/dQw4w9WgXcQ")).toBe("YouTube import (dQw4w9WgXcQ)");
  });

  it("falls back to a generic label when no id is present", () => {
    expect(defaultYoutubeTitle("https://www.youtube.com/")).toBe("YouTube import");
  });

  it("falls back to a generic label for an unparseable URL", () => {
    expect(defaultYoutubeTitle("not a url")).toBe("YouTube import");
  });
});
