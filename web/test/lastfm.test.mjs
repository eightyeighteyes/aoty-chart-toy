// Last.fm catalogue links (web/src/lastfm.js, AOTY-43). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {albumUrl, segment} = require("../src/lastfm.js");

// [artist, album, the "url" Last.fm's album.getInfo gave for it on 2026-11-27]
const FROM_LASTFM = [
  ["AC/DC", "Back in Black", "https://www.last.fm/music/AC%2FDC/Back+in+Black"],
  ["+44", "When Your Heart Stops Beating", "https://www.last.fm/music/%252B44/When+Your+Heart+Stops+Beating"],
  ["Taylor Swift", "1989 (Taylor's Version)", "https://www.last.fm/music/Taylor+Swift/1989+(Taylor%27s+Version)"],
  ["Sunn O)))", "Life Metal", "https://www.last.fm/music/Sunn+O)))/Life+Metal"],
  ["Sigur Rós", "( )", "https://www.last.fm/music/Sigur+R%C3%B3s/(+)"],
  ["Panic! at the Disco", "A Fever You Can't Sweat Out",
   "https://www.last.fm/music/Panic!+at+the+Disco/A+Fever+You+Can%27t+Sweat+Out"],
  ["The Dismemberment Plan", "Emergency & I", "https://www.last.fm/music/The+Dismemberment+Plan/Emergency+&+I"],
  ["Tool", "10,000 Days", "https://www.last.fm/music/Tool/10,000+Days"],
  ["Pg.99", "Document #8", "https://www.last.fm/music/Pg.99/Document+%238"],
  ["Why?", "Alopecia", "https://www.last.fm/music/Why%3F/Alopecia"],
  ["Ke$ha", "Animal", "https://www.last.fm/music/Ke$ha/Animal"],
  ["*NSYNC", "No Strings Attached", "https://www.last.fm/music/*NSYNC/No+Strings+Attached"],
  ["Godspeed You! Black Emperor", "F♯ A♯ ∞",
   "https://www.last.fm/music/Godspeed+You!+Black+Emperor/F%E2%99%AF+A%E2%99%AF+%E2%88%9E"],
  ["Brian Eno", "Ambient 1: Music for Airports", "https://www.last.fm/music/Brian+Eno/Ambient+1:+Music+for+Airports"],
  ["Fiona Apple", "When the Pawn...", "https://www.last.fm/music/Fiona+Apple/When+the+Pawn..."],
  ["Jeff Rosenstock", "WORRY.", "https://www.last.fm/music/Jeff+Rosenstock/WORRY."],
  ["!!!", "Myth Takes", "https://www.last.fm/music/!!!/Myth+Takes"],
];

test("album links match Last.fm's own URLs", () => {
  for (const [artist, album, url] of FROM_LASTFM) assert.equal(albumUrl(artist, album), url, `${artist} / ${album}`);
});

test("nothing in a name can leave its path segment", () => {
  for (const name of ["a/b", "a?b", "a#b", "a%b", "a\\b", 'a"b', "a<b>", "a\nb"]) {
    assert.doesNotMatch(segment(name), /[/?#\\"<>\s]|%(?![0-9A-F]{2})/, name);
  }
  assert.equal(segment("100%"), "100%25");
});

test("odd names still make a URL", () => {
  assert.equal(albumUrl("", ""), "https://www.last.fm/music//");
  assert.equal(segment("  two  spaces "), "++two++spaces+");
  assert.equal(segment("🎸"), "%F0%9F%8E%B8");  // outside the BMP
  assert.equal(segment(null), "");  // never throws on a bad chart value
  assert.equal(segment(undefined), "");
});
