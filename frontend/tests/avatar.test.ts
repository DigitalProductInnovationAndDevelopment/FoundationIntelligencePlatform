import assert from "node:assert/strict";
import test from "node:test";

import { avatarInitials } from "../src/lib/avatar.js";


test("authenticated avatar initials use meaningful email components", () => {
  assert.equal(avatarInitials("manuel.grabmayer@tum.de", true), "MG");
  assert.equal(avatarInitials("john.smith@example.com", true), "JS");
  assert.equal(avatarInitials("anna-maria@example.com", true), "AM");
  assert.equal(avatarInitials("max_mueller@example.com", true), "MM");
  assert.equal(avatarInitials("manuel@tum.de", true), "MA");
  assert.equal(avatarInitials("x@example.com", true), "X");
});

test("avatar initials safely fall back for malformed and guest identities", () => {
  assert.equal(avatarInitials("!!!@example.com", true), "AU");
  assert.equal(avatarInitials(null, true), "AU");
  assert.equal(avatarInitials("manuel.grabmayer@tum.de", false), "NG");
});
