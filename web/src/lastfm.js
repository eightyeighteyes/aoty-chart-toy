// Links to Last.fm's catalogue pages (AOTY-43). Last.fm's API terms (2.7) ask pages showing album
// info to link the album's Last.fm page. Pure: tested by web/test/lastfm.test.mjs.
(function (root) {
  "use strict";

  // One path segment as Last.fm writes it. Checked 2026-11-27 against album.getInfo's "url" for
  // awkward names: spaces are "+", so a literal "+" is encoded twice (%252B: "+44"), "'" is %27,
  // and $ & , : ; = @ stay as they are. encodeURIComponent already leaves ! ( ) * - . _ ~ alone.
  const RAW = {"%24": "$", "%26": "&", "%2C": ",", "%3A": ":", "%3B": ";", "%3D": "=", "%40": "@"};
  function segment(name) {
    return encodeURIComponent(String(name ?? ""))
        .replace(/%2B/g, "%252B")
        .replace(/%20/g, "+")
        .replace(/'/g, "%27")
        .replace(/%(24|26|2C|3A|3B|3D|40)/g, m => RAW[m]);
  }

  // The album's page, https://www.last.fm/music/<artist>/<album>.
  function albumUrl(artist, album) {
    return `https://www.last.fm/music/${segment(artist)}/${segment(album)}`;
  }

  const api = {segment, albumUrl};
  root.aotyLastfm = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
