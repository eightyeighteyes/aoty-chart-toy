# Changelog

## [1.11.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.10.1...v1.11.0) (2026-11-28)


### Features

* **api:** page backgrounds: stored, random for new pages, owner-set (AOTY-96) ([3faec6d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3faec6d929e73edd8264f05c3e65434363ee036e))
* bokeh is a page background, a pick and a random start (AOTY-102) ([0f4c362](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0f4c3626c9a2fa6774439c42751caec481c34d2a))
* chapel is a page background, a pick and a random start (AOTY-103) ([1b65640](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1b656401ef5b2607b2e2de12a58cc11d51543172))
* **chart:** charts carry the page's background (AOTY-96) ([522f8d7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/522f8d703a3ef73a6059f35fb89f834639d80741))
* page backgrounds, stored per page and set by the owner (AOTY-96) ([24f3033](https://github.com/eightyeighteyes/aoty-chart-toy/commit/24f3033ed7fe30d1380a879228d46662c958c8d1))
* **web:** background registry, loaded on demand (AOTY-77) ([3d6eb24](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3d6eb242faa79338cefab25c4db441e2a7e0c3a7))
* **web:** Bokeh background, on a new Canvas 2D base (AOTY-102) ([774a437](https://github.com/eightyeighteyes/aoty-chart-toy/commit/774a437b56104d53bf8e873541a49f8907a921e2))
* **web:** Bokeh, kamp's soft drifting orbs, on runCanvas (AOTY-102) ([d6936df](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d6936df323e45386c734241bdfeca8de1c768817))
* **web:** Chapel background, on a shared WebGL loop (AOTY-103) ([c623c01](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c623c01faddd19022556b8185b50daa30d6b7d3e))
* **web:** Chapel, kamp's breathing colour fields, on runShader (AOTY-103) ([5d0986f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/5d0986f7d17b1ec21ecc7ab196fc1aae8c3ebfef))
* **web:** load backgrounds on demand from a registry (AOTY-77) ([636ec6b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/636ec6bf92849ae724aecc38c257262d1dec5eb9))
* **web:** share layout and image preview (AOTY-98) ([0c5267a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0c5267aa68679eb54450709402a5a20740001cbe))
* **web:** the owner's background picker (AOTY-96) ([223163b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/223163b294d8ab409c7f80b8ac163e17b25452b6))
* **web:** the page's background on first visits and charts (AOTY-96) ([f273e37](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f273e3727500d2664f629484610db6f6fd32a02e))


### Bug Fixes

* **web:** the share image preview uses a data: URL (AOTY-106) ([80813b4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/80813b47689c9de2a6e5759b383fc0dec69fcd7a))
* **web:** the share image preview uses a data: URL (AOTY-106) ([f424b4c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f424b4cc898390036d45af31fcd01ad93ca280a5))

## [1.10.1](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.10.0...v1.10.1) (2026-11-28)


### Bug Fixes

* **card:** load the packed page modules as CommonJS (AOTY-104) ([f3102c4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f3102c45ab1c9aaad8b85760b078e3cf00c8a59f))
* **card:** load the packed page modules as CommonJS (AOTY-104) ([121f9d6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/121f9d614f983b2e6733864281213c7a941c7596))

## [1.10.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.9.0...v1.10.0) (2026-11-28)


### Features

* **api:** group limits and pause_groups (AOTY-53) ([4215f44](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4215f449762d68c1d3c9ba97deafdacec60330bc))
* **api:** members can leave a group and block future adds (AOTY-49) ([09cc661](https://github.com/eightyeighteyes/aoty-chart-toy/commit/09cc661e20a78b21b6dc460aca12ef47c472f2b4))
* **card:** a Node Lambda that renders the preview card (AOTY-91) ([652f687](https://github.com/eightyeighteyes/aoty-chart-toy/commit/652f6875b506e81f106ec8c6d476e096a969fbb6))
* **card:** link previews use the landscape card (AOTY-95) ([05a5024](https://github.com/eightyeighteyes/aoty-chart-toy/commit/05a50249ed209669a5365843cf7a11abc3015f89))
* **chart:** a link preview page beside every chart (AOTY-85) ([961397e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/961397e1bdb433df0f85883412d2e50154a9fe7b))
* **chart:** use the rendered card in link previews (AOTY-91) ([995d376](https://github.com/eightyeighteyes/aoty-chart-toy/commit/995d376bee2171d18bf75957ca3298261b7760cf))
* consent before analytics (AOTY-28) ([9e06325](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9e063253d2eddaf11d817357cbfb88dcdf007512))
* guardrails and alarms for groups (AOTY-53) ([bbd0b8b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bbd0b8b3b068a42584a345fe967eab63833c55ad))
* **guardrails:** group knobs (AOTY-53) ([281b027](https://github.com/eightyeighteyes/aoty-chart-toy/commit/281b02751d24cdc0b9ece88c9f1cb35eebf6d2ce))
* **infra:** alarm on dropped recursive invocations (AOTY-58) ([0d872a7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0d872a74f5dffc693210e287c00168d6e1f4f753))
* **infra:** alarms for sign-in and chart failures (AOTY-53) ([6f51487](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6f51487e6f5d55c32ca885450c156d687a6d288b))
* **infra:** the api may batch-read group metas (AOTY-49) ([670905e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/670905e6c46c09caff80912f4d967c01e10eba59))
* **infra:** the card Lambda (AOTY-91) ([a06d4f8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a06d4f834db8c909701f341a5c99d1bcab37d38d))
* **infra:** visits by page type on the dashboard (AOTY-64) ([1d30da1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1d30da1b4b7d702c7da488791222ad7185138052))
* **infra:** visits by page type on the dashboard (AOTY-64) ([3a50cbe](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3a50cbe8f5f118740b1def8e48099ad8b0be91e4))
* landscape 1200x630 link preview card (AOTY-95) ([327a6e1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/327a6e1a39ed79aad84539c933d6618c1ee23ccf))
* leave groups and block future adds (AOTY-49) ([73d29a6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/73d29a6cbe8c12a9c89e3e775c144f8fc94032bf))
* link previews show the chart's [#1](https://github.com/eightyeighteyes/aoty-chart-toy/issues/1) (AOTY-85) ([d48c360](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d48c3600f5be4454359054c8b901e61ea3a6107e))
* metrics for sign-in failures and group builds (AOTY-53) ([378bdc6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/378bdc6cab8cf3c588dcf0b7e70af78074ea658d))
* rendered share cards in link previews (AOTY-91) ([e046a0d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e046a0d00f1fd4d01cb37f327b0cef282efc8e55))
* **tools:** load test with groups (AOTY-53) ([41aab77](https://github.com/eightyeighteyes/aoty-chart-toy/commit/41aab77b412dcf6a778969e9095f638c2e96fe85))
* **web:** a landscape 1200x630 share card layout (AOTY-95) ([41b10d4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/41b10d4275d0fea67ef824ec5dddcc5b36eea073))
* **web:** a shared consent choice and banner (AOTY-28) ([7d33a5f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7d33a5f25ed09543ade0a1e82b8055e15713bf3a))
* **web:** a stalled first visit keeps watching (AOTY-79) ([bd94a07](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bd94a07bb09f3b378c4b3ab34f89416c2ef9ae3d))
* **web:** generic preview tags in the shell (AOTY-85) ([0e024ea](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0e024ea37edfdfc0e08c60cadff7cd480cb39289))
* **web:** leave groups and block adds from "your groups" (AOTY-49) ([45df5a1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/45df5a17f40d61295126f23d1c87d3ebbae681b2))
* **web:** load analytics only after consent (AOTY-28) ([87d6f9f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/87d6f9fdfe72555cf63425a94725d1ba8e090eb0))
* **web:** short share links for names that start with an underscore (AOTY-89) ([7855b1c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7855b1cc0cd0ddca7fa524f3426dd5828a91eefc))
* **web:** short share links for underscore names (AOTY-89) ([5bede4f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/5bede4f37d20d1e3eed4d09e4e4e115a9c5edc18))
* **web:** the consent banner reads kamp.fm's colour tokens too (AOTY-28) ([7ccc6b1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7ccc6b1f1cdf8c47605a2d3db24b40f2b2230c11))
* **web:** wait_abandoned and the events audit (AOTY-28) ([1392a47](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1392a476031898a36be2ae7453764fe87b1a27d1))


### Bug Fixes

* accept Last.fm usernames that start with an underscore (AOTY-80) ([80922cf](https://github.com/eightyeighteyes/aoty-chart-toy/commit/80922cfd3c04f00749bb553510a7d96517e071cb))
* accept Last.fm usernames that start with an underscore (AOTY-80) ([99cb73b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/99cb73bb52b6879536a8709471250efa755ed9eb))
* **infra:** start the mb lane every 5 minutes (AOTY-58) ([033f626](https://github.com/eightyeighteyes/aoty-chart-toy/commit/033f626dcb8007ea744046cb036ffdfffa4171a4))
* **mb:** stop invoking itself; the schedule starts the next run (AOTY-58) ([cd5d174](https://github.com/eightyeighteyes/aoty-chart-toy/commit/cd5d1740ea93e7d732522076ee398667d27d1170))
* **mb:** stop the self-invoke chain; start runs from a 5-minute schedule (AOTY-58) ([a805a03](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a805a03a3da72899f58f2e025a13ca3952675b5b))
* never tell waiting people to close the tab (AOTY-79) ([d38bb4b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d38bb4be91333d45f471cd33741db3352c92756d))
* **web:** give the intro its own main landmark (AOTY-45) ([96cf1a2](https://github.com/eightyeighteyes/aoty-chart-toy/commit/96cf1a227221115f73d98757749def659ee6614a))
* **web:** give the intro its own main landmark (AOTY-45) ([b6c871c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b6c871cf55eaf29429ce251184c38220a57f4dd2))
* **web:** the landscape card's caption and link line up with its names (AOTY-95) ([46c9940](https://github.com/eightyeighteyes/aoty-chart-toy/commit/46c9940c58d7130a434401b5f972b2e489de6ce9))
* **web:** the queue line keeps people on the page (AOTY-79) ([0c559f7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0c559f7ec76f59d3753bf309b22055629d265164))

## [1.9.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.8.0...v1.9.0) (2026-11-27)


### Features

* **build:** short host for prod share links ([e230160](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e23016080f4a40a1987fd23f8edf18989dec9108))
* **chart:** carry each album's Last.fm name ([2e797c9](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2e797c9c2a0c61b79613a53064cf676cbe819b65))
* link chart albums and names to Last.fm (AOTY-43) ([ccf0aad](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ccf0aad96f9cdd1c04bedaafeddbb72d30700d49))
* share links for user and group pages (AOTY-84) ([c877daa](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c877daa2ef70e8d124ecf397a0d4af18de64b052))
* **web:** 9:16 story share images (AOTY-72) ([334f9c6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/334f9c62beeb78b2096a418f3e36491de5283c69))
* **web:** a 9:16 story layout for the share image (AOTY-72) ([0867d13](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0867d133c491b05cef289ccbd6b4768e582a123e))
* **web:** count the story format and its switch (AOTY-72) ([c562ba1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c562ba1998753b0432e97e916972ab67a6adb280))
* **web:** draw and share the top-album image (AOTY-70) ([01eb223](https://github.com/eightyeighteyes/aoty-chart-toy/commit/01eb223a4e13be63ee13ef33efdcf3609c8421d8))
* **web:** Last.fm album URLs ([816c6b9](https://github.com/eightyeighteyes/aoty-chart-toy/commit/816c6b96a6c51f381cb04fd44433a34bbb7c9bdd))
* **web:** link albums and names to Last.fm ([fba6702](https://github.com/eightyeighteyes/aoty-chart-toy/commit/fba6702b707f65d9983195200a9c8bf69a0939ae))
* **web:** pick a square or story share image (AOTY-72) ([af06a6f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/af06a6f0fd276d026d5dbc28b0c067c3bdb3464c))
* **web:** share a group's [#1](https://github.com/eightyeighteyes/aoty-chart-toy/issues/1) as a square image (AOTY-71) ([5875f28](https://github.com/eightyeighteyes/aoty-chart-toy/commit/5875f28069298925aa1517647759b1b9a5ff32d0))
* **web:** share a group's [#1](https://github.com/eightyeighteyes/aoty-chart-toy/issues/1) as a square image (AOTY-71) ([a855e20](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a855e203075fdafc7ae229db7e88918f89a6de89))
* **web:** share button on user pages, native share on touch ([8ac0c9c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8ac0c9c34b5047856eb4bed0f25c63f19790db2d))
* **web:** share card layout (AOTY-70) ([7af4c3b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7af4c3bb92e850c6b067f616e0b6c33ac2ee7b25))
* **web:** share image button and its rules (AOTY-70) ([6bc13d8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6bc13d817aac9c02de61e50e4b1d54fa44939c3f))
* **web:** share links, short on prod ([8fdbd2c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8fdbd2c75c450aeb7f2b641d0758a3577e514adb))
* **web:** share your [#1](https://github.com/eightyeighteyes/aoty-chart-toy/issues/1) as a square image (AOTY-70) ([3630adc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3630adc7c84ede2cb6a11c5da33152847ec9b44c))
* **web:** share your top 10 as an image (AOTY-74) ([30c29c5](https://github.com/eightyeighteyes/aoty-chart-toy/commit/30c29c5d8f49418d7d45a9559cc779ad0a9c205c))
* **web:** share your top 10 as an image (AOTY-74) ([69b0ba6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/69b0ba6d1e1a5bb5df70bf8a81705b8c944744e1))
* **web:** share your top 3 as an image (AOTY-73) ([6608815](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6608815af2b05cae3a9034d4fba6d3950565c5a3))
* **web:** share your top 3 as an image (AOTY-73) ([622a471](https://github.com/eightyeighteyes/aoty-chart-toy/commit/622a471684a67021bac82e7533b92059b5085bda))
* **web:** the group's on-screen [#1](https://github.com/eightyeighteyes/aoty-chart-toy/issues/1) for the share image (AOTY-71) ([86e1a31](https://github.com/eightyeighteyes/aoty-chart-toy/commit/86e1a31c797ee1592916d6e788369148a0d4a66e))
* **web:** the top 3 on screen and the image's content rule (AOTY-73) ([b4af94a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b4af94acd38bceb42cae3491878dca6b193f5100))
* **web:** top 10 in the image's content rule, falling back to top 3 (AOTY-74) ([f4e84ea](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f4e84eac33ab260d5cf04d0066e116bd131665d8))
* **web:** top 10 share card layouts (AOTY-74) ([9a38724](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9a38724ac3e6afb8c1bc7fd0afd493419342801d))
* **web:** top 3 share card layouts (AOTY-73) ([63f319c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/63f319c152213149cd4ad5533e74d39d953af637))

## [1.8.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.7.0...v1.8.0) (2026-11-26)


### Features

* **tools:** rebuild every chart in an env ([b0275d5](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b0275d51f036abc3823490b577d7bcb13c94e47f))


### Bug Fixes

* **chart:** count short EPs and other non-album types as singles ([84b3e79](https://github.com/eightyeighteyes/aoty-chart-toy/commit/84b3e7976c0a49bff6446dd684a49e109f022341))
* **chart:** count short EPs and other non-album types as singles (AOTY-82) ([2ce1d27](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2ce1d27bf2fe64caa9564a87c169f1922f091d85))

## [1.7.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.6.0...v1.7.0) (2026-11-26)


### Features

* a page's theme doesn't wait for its chart ([bf4a695](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bf4a6950b89495d8452167ee808ed549432cba6a))
* **api:** group owners save their page's theme ([127f539](https://github.com/eightyeighteyes/aoty-chart-toy/commit/127f5396f12bb3482b613f2e50540267d3e17f89))
* **api:** owners save their page's theme ([65005a1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/65005a197b31a286a61ce1ae4f08931bdb65fdbb))
* By Listener / By Play / Weighted rankings; By Track and singles go (AOTY-63) ([48dcac8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/48dcac89fcc9c6440e544129128c0a05c493b879))
* **chart:** charts carry their owner's theme; a restyle event ([9605589](https://github.com/eightyeighteyes/aoty-chart-toy/commit/960558974cfe4f6180a31e07eabb2defd47a37cc))
* **chart:** group By Album ranks listeners first, then album plays ([0251bcc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0251bcccec51ba32163760bf4816dcf4df989424))
* **chart:** group charts carry their theme; a group restyle ([e5dcf01](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e5dcf0109563ace457b5a2319148b51c9b766900))
* **chart:** rankings are By Listener, By Play and Weighted; no By Track ([ece8b42](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ece8b425a7043183174b710c1160434c69ddd670))
* group owners pick their group page's theme (AOTY-76) ([b589d0e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b589d0ed857d7e9ff89094c36105dfdbcfe3b210))
* owners pick their chart page's theme, kamp palettes and "album of the year" (AOTY-65) ([842e896](https://github.com/eightyeighteyes/aoty-chart-toy/commit/842e8961031445b403e7e43087c76fae17db8968))
* **web:** album of the year first, presets in rainbow order, poster reads "oranger" ([db87cb0](https://github.com/eightyeighteyes/aoty-chart-toy/commit/db87cb0a748922f0307ffb7280cc11558fa7c754))
* **web:** borealis.js, ported from kamp ([cd1c1be](https://github.com/eightyeighteyes/aoty-chart-toy/commit/cd1c1be26cb276e77d6f49fa9869fc0a214ef2c5))
* **web:** By Listener / By Play / Weighted switch on groups; user pages lose it ([003f1a1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/003f1a1191088d6181321c4387d5752c5cc0435e))
* **web:** group ranking copy names listeners first ([154d720](https://github.com/eightyeighteyes/aoty-chart-toy/commit/154d720c32456939cb1cab94b9e3299a6aee5e39))
* **web:** kamp's Borealis behind the first visit; GPLv3 (AOTY-67) ([f198800](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f198800081d2d8001fbfa8605ace04555da1a153))
* **web:** light themes, gabbie among them (AOTY-75) ([9b589ec](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9b589ecc9581f83dfebae7e6409cc90a43d1b514))
* **web:** remember a page's theme to avoid a dark flash ([c3e5ef6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c3e5ef6094b9858f510e10e99616d0d117936f8f))
* **web:** seven light themes, gabbie among them ([33566e8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/33566e86a8e59c7e1b64d45e0c0448bdd0d3776c))
* **web:** the aurora behind the first visit ([429d79c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/429d79cd37956b75bd6f05934da0f2cc27feb557))
* **web:** the cover wall drifts in rows ([c3d9afb](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c3d9afb95263dc92b7aa4701a6fdecaa23b3eb0f))
* **web:** the cover wall drifts in rows (AOTY-68) ([e3d46cb](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e3d46cb72e4fc916ca1521014261c5f36b61d3bb))
* **web:** the cover wall shows only covers that loaded ([fe74553](https://github.com/eightyeighteyes/aoty-chart-toy/commit/fe745537d584dd4d3780c3dc7704f30cfdc9fb3a))
* **web:** the cover wall shows only covers that loaded (AOTY-66) ([8619145](https://github.com/eightyeighteyes/aoty-chart-toy/commit/861914548d1d07828fef993996cc740090ac7722))
* **web:** the theme picker on group pages ([304a2a4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/304a2a459d77a01c902dc553ad69ae2553a52b26))
* **web:** theme picker and "album of the year" colours ([08c7032](https://github.com/eightyeighteyes/aoty-chart-toy/commit/08c7032e4b33ac217c2f1d22589f76b00902af4d))
* **web:** theme tokens and a pure theme module ([3edfe36](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3edfe361291505f30bdc053cdeb6b0e0e1b71fab))


### Bug Fixes

* **api:** give Last.fm 10 s to answer a new user's check ([26fe40d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/26fe40d324880df715a0c4bb04391bedbb7e0a7f))
* new users refused when Last.fm answers slower than 3 s (AOTY-81) ([cbd0068](https://github.com/eightyeighteyes/aoty-chart-toy/commit/cbd0068fe8efb3d040319c0e9578c94466e286b6))
* **web:** a just-queued first visit polls at the normal pace ([dc07155](https://github.com/eightyeighteyes/aoty-chart-toy/commit/dc071558528f1ff785c616cea67b2462b7ad4c86))
* **web:** in-line polling goes by place in line ([33c8bf9](https://github.com/eightyeighteyes/aoty-chart-toy/commit/33c8bf91fd4b35ae9d61ebe6e83e791276193310))
* **web:** in-line polling goes by place in line (AOTY-78) ([8fc8525](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8fc8525caf5fdd0f287b7fe0a984228d13d5d2bf))
* **web:** keep kamp's own colours wherever they pass contrast ([e742f28](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e742f28b814ec1a6cbcddb715b8606d515dc185b))
* **web:** say Last.fm isn't answering instead of "couldn't update" ([1b3713c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1b3713cc79b1761462b59386b6c7ef49b9fcdd57))
* **web:** the aurora no longer hides the page ([aa6f521](https://github.com/eightyeighteyes/aoty-chart-toy/commit/aa6f52198dc6c76750d3ab7277ccd91a2ef2dad0))
* **web:** theme save errors go by the answer's error word ([ecd26a7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ecd26a77ef4270ce9293c9dd28b07f4e23619d28))

## [1.6.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.5.0...v1.6.0) (2026-11-24)


### Features

* **web:** analytics on every page (AOTY-38) ([07a20dd](https://github.com/eightyeighteyes/aoty-chart-toy/commit/07a20dd6daf1685cf42a0a70843d4a873df2f80e))
* **web:** build the shell with an optional GA measurement id ([fa8148d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/fa8148d9449eac0bb8f2e299a2e050bd28f3fb37))
* **web:** kampstats analytics wrapper with templated paths ([a223f16](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a223f163ce6feada94323d1c8faf153cd6a1e673))
* **web:** page views and events on every page ([4bc7f03](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4bc7f035e65a4f4538694a661c4121cc1b881c87))

## [1.5.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.4.0...v1.5.0) (2026-11-24)


### Features

* **infra:** turn on group writes in prod ([f330d7f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f330d7f7f82a5cfd65ea966cdf3abc3aeec6c212))
* **infra:** turn on group writes in prod (AOTY-62) ([bae2b52](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bae2b523b8094920bc62216c9f2af78798a78bd2))

## [1.4.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.3.0...v1.4.0) (2026-11-24)


### Features

* a visit to a group page syncs its members (AOTY-55, AOTY-60) ([a1f3566](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a1f3566d5609e9a4c0c2a0872fe6e16836421cb4))
* **api:** a visit to a group page syncs its members ([f1cd5cc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f1cd5cce85e8f4e0d3028de6bf5ec55512119b13))
* **api:** api/me says whether group writes are on ([39975b2](https://github.com/eightyeighteyes/aoty-chart-toy/commit/39975b24c99ae3a7007495788d2d16e38d30d67a))
* **api:** owned groups: create, rename, delete, add and remove members ([d2843a8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d2843a83f6273f239238a3b1c909afd0bbcf7b95))
* **api:** sign in with Last.fm (AOTY-47) ([ee90909](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ee909099121dd0f8015d3c1ad64f1f7dd4d0ae3b))
* **api:** sign in with Last.fm: auth/start, auth/callback, me and signout ([a0d6191](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a0d61915eedbc729168c341b5ca72cd93ae9cf28))
* **api:** sign out everywhere revokes every session of the user ([bca5592](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bca5592f009d0e47b525333696260fcc30666639))
* **api:** sign-in routes, the landing-path check and Last.fm's auth URL ([b908048](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b908048730a4fdb58e19a0d25288e160de76e2c9))
* **api:** signed session and sign-in state tokens and cookies ([6f9cc25](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6f9cc25244b3cb3f0dd26dd1d15e07dafec9abac))
* **chart:** build group charts from the groups table ([9b52166](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9b521665de5a51e1f7580cb063423ee2187238c1))
* group editing UI: sign-in row, create, add and remove members, share (AOTY-52) ([1683384](https://github.com/eightyeighteyes/aoty-chart-toy/commit/16833843f86be57ac0561d65848b3659a9e42825))
* **infra:** groups table with member items and a prod write gate ([0975474](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0975474dec19b7f6f262c76d19e1320e803ac83c))
* **infra:** lock prod's api to kamp.fm's CloudFront ([500ba72](https://github.com/eightyeighteyes/aoty-chart-toy/commit/500ba726aebbf9af69170f397570f9a2755ff284))
* **infra:** lock prod's api to kamp.fm's CloudFront ([2a36588](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2a365882ec655f13e0fc1eaad7aa0682cb71dfe7))
* **infra:** lock staging's api to kamp.fm's CloudFront ([306b94a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/306b94ad6d4f62028c710e6690e8c36bc54cbbc9))
* **infra:** lock staging's api to kamp.fm's CloudFront ([e9ccc74](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e9ccc74ae2a64775a232265e9f9c0ee4467a21bf))
* **infra:** sign-in secrets readable by the api alone, and its callback origin ([88ed46e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/88ed46e0af99cdd1538d8ef605a56e8fab5ce72a))
* **lastfm:** signed calls and auth.getSession for sign-in ([0a1042e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0a1042e656354b2ced90abbc2345346af1d84e8c))
* owned groups table and API (AOTY-48) ([57989ab](https://github.com/eightyeighteyes/aoty-chart-toy/commit/57989abb3fbdc427d32f7e88957a1c08b884ab0a))
* **session:** a revocation cutoff that a session's iat must be after ([693bdfd](https://github.com/eightyeighteyes/aoty-chart-toy/commit/693bdfd63d458d45caa4b0a8f337eb9fef8cabb5))
* sign out everywhere (revocable sessions) ([1b93613](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1b936136e0bce54c8ba3f86608dfd3c09fa0e187))
* **tools:** pre-warm the albums table (AOTY-42) ([ee4462d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ee4462d0c0c7d236763a040d69595c4c16bbfc30))
* **tools:** pre-warm the albums table from Last.fm's year tag and top artists ([1a626b1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1a626b141658c095219bd48f7e7129c14237f47e))
* **tools:** preview a group page visit's member syncs ([9226a7a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9226a7aa9e113a5f172315882a9c281e8e378d1b))
* **tools:** preview sign-in and group editing in serve_shell ([961c6a4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/961c6a4f91a8138e3971c997acda8dc456bcb1fe))
* **web:** a group page visit syncs its members ([1084163](https://github.com/eightyeighteyes/aoty-chart-toy/commit/10841638ae124c35b9a6b05bd23f279405d99ae0))
* **web:** a sign out everywhere button ([e2ff61b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e2ff61b8ae711664dda94c8948aa4a91fd4ad7f2))
* **web:** sign-in row, your groups, and the owner's group editing ([c62eda7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c62eda71337b04cee5a8c1c9fad7ea5544c1b34c))


### Bug Fixes

* **lastfm:** a made-up sign-in token no longer counts as a Last.fm error ([2f468a2](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2f468a22d01027b848dc8fe7d9548ac0f07c631e))
* **lastfm:** a made-up sign-in token no longer counts as a Last.fm error ([7b4e03a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7b4e03a1ea5ec9c5bd22809e0ef1d76942b27679))
* **tools:** --sign-in-as lands back under the preview's base, not sandbox's ([0d7cd11](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0d7cd118cda03a601640431c9179846239b32adb))


### Performance Improvements

* **chart:** a burst of member syncs rebuilds each group once ([a2b8fa2](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a2b8fa232c27b48ef6d5fdbabfd4896e23338bfa))

## [1.3.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.2.0...v1.3.0) (2026-11-23)


### Features

* alarms, uptime check and budget (AOTY-27) ([5722c1b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/5722c1b09de90b33bb10f407eadbbc9833faa284))
* **api:** pause new users and syncs without a deploy ([0127c07](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0127c07aa556e1037479d0cd34cb15d094a729cb))
* **api:** per-IP limit on new usernames ([461f55d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/461f55d87a1c5d133010bee97f89dedf94d4f314))
* **api:** queue position and measured wait ([1f72c6f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1f72c6f7fc258f13e7401f47799c662018936e5d))
* **api:** refuse user routes that bypass CloudFront ([b88c0f9](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b88c0f9571a89c7ceb46452a0edcb5a13e4aa008))
* **api:** runtime guardrail settings from SSM ([e6e4db6](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e6e4db67cb1fe26867ffc8b4416ded37b1683819))
* **chart:** beta group chart with M0's rules (AOTY-50) ([af4c760](https://github.com/eightyeighteyes/aoty-chart-toy/commit/af4c76016bcf213a6a9cbc9e02c57ed317fddb8d))
* **chart:** group charts for a hand-made beta group, with M0's rules ([744c6bf](https://github.com/eightyeighteyes/aoty-chart-toy/commit/744c6bf6d486e1afc05ab56ab73458f7becc112b))
* CloudWatch dashboard per env and sync/api metrics (AOTY-41) ([ef122df](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ef122df75b51bffff7252814b17501142164a070))
* credits footer and New Bands for Old Heads in the header (AOTY-29) ([a026980](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a02698086710bcda331e89b9b8d7533df5a1a042))
* **groups:** readable slugs for group URLs instead of random ids ([3bad5f0](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3bad5f0a2c96e77d839fc6979446e6b9a7b7a0be))
* **infra:** a CloudWatch dashboard per env; chart-event-age in milliseconds ([f8a79db](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f8a79dbb2e2d7c37bfe0280abc32b56e4e5e1b72))
* **infra:** alarms, alert topic and monthly budget ([31dbc55](https://github.com/eightyeighteyes/aoty-chart-toy/commit/31dbc5588566444465dc3a1f5acd57be32281f1b))
* **infra:** one setting caps sync workers ([d74d775](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d74d775cf54e37bced68dc55226eb8a52e7bf207))
* **lastfm:** LastfmErrors metric for failed Last.fm calls ([7c2a35f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7c2a35f30924c652ee00ef006e090caa78ea134c))
* **metrics:** api EMF for sync requests, counted at the dispatcher ([06055ee](https://github.com/eightyeighteyes/aoty-chart-toy/commit/06055ee044c21c42a7253281e55e407cee490649))
* **metrics:** Last.fm calls from chart and mb, fake renamed, unknown dump age left out ([b2662f8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b2662f87af8b828af39af7ac2141817226586e89))
* **metrics:** sync worker EMF and a shared emitter ([bced649](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bced649cb18b59144be69a0085fdd352c05dcb6f))
* surprise-hit guardrails, kill switch and queue screen (AOTY-26) ([69cb678](https://github.com/eightyeighteyes/aoty-chart-toy/commit/69cb678930fb68dc3997d13beecb0b62dea1900f))
* **uptime:** hourly uptime check of kamp.fm ([ae6dd0d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ae6dd0d0de17f9077b78a8cfa1a268632c6f5069))
* **web:** 'still counting deep cuts' instead of big pending numbers ([a751fc1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a751fc14eb79177ed73abb007152cd1916de76ea))
* **web:** 'still counting deep cuts' instead of big pending numbers ([77984b8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/77984b8e4451b521da4cf6b2b7fe28d5f9805285))
* **web:** credits footer on every page ([3bed482](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3bed482f49cff6088259b38a65ff923c8b41fee0))
* **web:** group page for the Discord beta (AOTY-51) ([40c4483](https://github.com/eightyeighteyes/aoty-chart-toy/commit/40c44831c1685f770cf44fc3e5d5056d52698534))
* **web:** group page with Weighted, listener dots and the member filter ([8716a7f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8716a7f639b980071e9e23daf966696c0e3be15a))
* **web:** last.fm profile url helper ([14d9b2f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/14d9b2fa994105fb972a383ee68d4544956c6786))
* **web:** New Bands for Old Heads endorsement in the header ([4bef27e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4bef27ed4dbee80232f9f93aadd4998af0d93602))
* **web:** request-line queue screen ([a8f7f2e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a8f7f2e35730b401e37ea7b632b2a74c06c6ee52))


### Bug Fixes

* **api:** a long queue no longer reads as a dead sync ([70cf04a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/70cf04a68298401f6d56e4046e958b27ec565cfd))
* **chart:** newly dated albums reach group charts (AOTY-50) ([99312a7](https://github.com/eightyeighteyes/aoty-chart-toy/commit/99312a7f426b82058556421171b5255f218d64e4))
* **chart:** newly dated albums reach group charts in the pending pass ([a2ca0ac](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a2ca0ac6e01bf01af060bfefb495dcaa8b7cb443))
* **chart:** show the root album title, not edition suffixes ([7e21a7f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/7e21a7f2a2153dcc989cf2077dbe2358aeaabf63))
* **sync:** continue a long sync in a fresh message, not a re-receive ([b7420a9](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b7420a918e90e13cfa44c07399b4435b694e0a2b))
* **sync:** continue long syncs in fresh messages so they never reach the DLQ (AOTY-46) ([1dc7cae](https://github.com/eightyeighteyes/aoty-chart-toy/commit/1dc7cae48c6637fbe4c3aaaa80af990fd3ead8ee))
* **web:** group cards lead with listeners and fill dots in order, as M0 ([72b1cdc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/72b1cdccf5291c3d53cd5d883bd96d3bc2e45a96))

## [1.2.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.1.0...v1.2.0) (2026-11-23)


### Features

* **api:** POST sync and GET status routes ([89120f3](https://github.com/eightyeighteyes/aoty-chart-toy/commit/89120f3590ca99d3185e01f61d63e04b6a125674))
* **api:** pure core for sync requests and status ([ad0d52b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ad0d52b75efeaa984484236c3c689b862cee8951))
* **api:** status carries the first-visit cover wall and running stats ([fdd0506](https://github.com/eightyeighteyes/aoty-chart-toy/commit/fdd05064a64cd645ee69c473c36a7d4e03b8ec6b))
* **chart:** chart_plays, chart_albums and chart_hits on the users row ([d2b128f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d2b128fe95d0572ed810ea90006455d7afef28ba))
* first-visit cover wall and progress (AOTY-24) ([840ba56](https://github.com/eightyeighteyes/aoty-chart-toy/commit/840ba56f3a5cda4fdcc97966506ae2ad9811c1a1))
* **infra:** sync cooldown, queue depth for the api, TTL on users ([aaf06b1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/aaf06b1f4675c22ef065c89bd6833929ec5195ff))
* intro page and kamp endorsement chrome (AOTY-23) ([4ac0510](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4ac05101073a59d2b025831b758201b7362fbdf8))
* sync on visit with a status API and updating banner (AOTY-22) ([2677b1f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2677b1f769887ef3db2303be298bf1c3f60d621f))
* **sync:** first-visit cover wall and per-week progress with running stats ([f2abfd5](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f2abfd5256ffb53ce286e257a80e8279da8642f9))
* **sync:** record the finish and live play counts for the status API ([534642f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/534642ff870789cd3fe5b5be8ec02cfe91ab8ac7))
* **tools:** preview every intro message in serve_shell ([d6cf401](https://github.com/eightyeighteyes/aoty-chart-toy/commit/d6cf40104febfb230803bf2282a8f6b3259f92b6))
* **tools:** preview first visits in serve_shell through the real status code ([77547a3](https://github.com/eightyeighteyes/aoty-chart-toy/commit/77547a3599d7aa7fa7a91cb3699745c1e038f709))
* **tools:** serve_shell --proxy sandbox ([46ff42c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/46ff42ca4f299625813f8ffdf45c172c684de68c))
* **web:** first-visit cover wall and progress while a first chart is pressed ([563d755](https://github.com/eightyeighteyes/aoty-chart-toy/commit/563d755da3a23e17dee43aa17fdece943cf04106))
* **web:** intro page and the kamp endorsement bar ([a924959](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a924959fa22920732bd3c6791409f857997f2f06))
* **web:** intro page rules, tested with node --test ([2adfe8f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2adfe8f0f050678d4c29b36dcace8e2d07e56f49))
* **web:** sync on visit with a non-blocking banner and in-place repaint ([da72538](https://github.com/eightyeighteyes/aoty-chart-toy/commit/da725383649cc50ee8c7ef3d82e1c05282b0bdac))


### Bug Fixes

* **web:** keep body from painting as one flat colour (Chrome tile seam) ([c48fbfc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c48fbfca79315f06cd33d22e0a13466e5ea85d44))
* **web:** no overscroll bounce, which showed as a colour seam in Chrome ([0dd1f81](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0dd1f811b06d4780115ec2a530b44459c4e89e9d))
* **web:** paint the intro and its form so a focused field shows no seam ([6aa0290](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6aa0290e846bb56ac8461b454ca9386839ed7676))
* **web:** stop waiting when a sync ends without a chart ([3764a73](https://github.com/eightyeighteyes/aoty-chart-toy/commit/3764a73097e46f0ca571db21ea81a2541fe63d99))

## [1.1.0](https://github.com/eightyeighteyes/aoty-chart-toy/compare/v1.0.0...v1.1.0) (2026-11-23)


### Features

* **api:** report the deployed SHA from /api/health ([01fad8a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/01fad8a684dc683b87ae70b70e772c2f90977f0c))
* chart builder and user page (AOTY-21) ([917b92e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/917b92e55f5d2dc031660ffc72bbdee0b3704784))
* **chart:** album.getInfo covers for shown albums with none ([caa7340](https://github.com/eightyeighteyes/aoty-chart-toy/commit/caa734040356908ea7a867c09302146d9386d5bb))
* **chart:** chart builder Lambda handler ([b98a1ca](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b98a1ca73fcb317374b7aa492f8592114de0a486))
* **chart:** rank one user's store into chart.json ([642da94](https://github.com/eightyeighteyes/aoty-chart-toy/commit/642da944dc74c179044836619fa9324a38bef6d6))
* **ci:** deploy any branch to sandbox ([eeda30d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/eeda30d151f3a7cf12e00067dc3e049937587c81))
* health endpoint reports the deployed git SHA (AOTY-35) ([f82d406](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f82d4063cd3056ed4bf1cebf5386f4ecefb03f42))
* **index:** add 2026 release-group search crawler ([bf3619f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bf3619f9ea873d0d9531a194733fefe9fd9f9903))
* **index:** add index hit-rate evaluation ([6108607](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6108607976b184d988cb787fd11f8ab899bffd6a))
* **index:** add MusicBrainz JSON dump index builder ([15c827d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/15c827de62ae1cb49969bebcb71942a507f13c8b))
* **index:** AOTY-13 MusicBrainz 2026 release-group index spike ([ec4a76d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ec4a76d8209c136e9e70efc7c82502b871bbd02a))
* **index:** dump-to-S3 index builder with re-check queueing ([8a5cbd0](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8a5cbd092202850e5721278e0b29979509fd3c16))
* **index:** MusicBrainz release index from the JSON dump (AOTY-20) ([9eccbbb](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9eccbbb8b92113489feb9858ec886978e4771ac3))
* **index:** sharded release index format and lookup ([506a358](https://github.com/eightyeighteyes/aoty-chart-toy/commit/506a358bd34e7a1494814ef24634f6d19855f091))
* **infra:** add sandbox environment root ([ef2d358](https://github.com/eightyeighteyes/aoty-chart-toy/commit/ef2d358e62674b43fc28f9e84c1767acd6018bf5))
* **infra:** chart builder function, role and invoke grants ([9f992dd](https://github.com/eightyeighteyes/aoty-chart-toy/commit/9f992dd4f70445e8ce1e0771872771eafcc1a661))
* **infra:** Fargate Spot index build on a daily schedule ([0c1eb9b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0c1eb9b85f598895e5125e65242bab6a11312eae))
* **infra:** let deploy roles manage the mb backstop schedule ([aa3fe31](https://github.com/eightyeighteyes/aoty-chart-toy/commit/aa3fe31ad6019231d6de762b1ee54f26844c5809))
* **infra:** mb reserved concurrency, invoke config and hourly schedule ([aab96d8](https://github.com/eightyeighteyes/aoty-chart-toy/commit/aab96d8456755dd39aa447aed15b31f820b31feb))
* **infra:** sandbox deploy role and tag-scoped HTTP APIs ([69e9e21](https://github.com/eightyeighteyes/aoty-chart-toy/commit/69e9e212d8b535aeedc0a4487abf01d3b8c56550))
* **infra:** sync queue event source mapping ([0818a25](https://github.com/eightyeighteyes/aoty-chart-toy/commit/0818a2520a85393f2e834abe398462a708627a51))
* **lambdas:** Last.fm album track count ([a5e4575](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a5e4575f37bc9a3419961828c61111b785cd3ea3))
* **lambdas:** standard-library MusicBrainz client with a deadline ([6f9c61c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6f9c61c7b609898fccccc01ce64ca679560f847a))
* **mb:** MusicBrainz lane, single-client release-year resolver (AOTY-19) ([8e4f821](https://github.com/eightyeighteyes/aoty-chart-toy/commit/8e4f821cbe32d1752839b1f010fa89f2a5cbfcd0))
* **mb:** poke the chart builder as albums are written ([b5b9013](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b5b90134f4741c81461cef66a656f7e2e1bdf534))
* **mb:** queue-draining handler ([a3281b5](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a3281b539d8029949e9b8edd42f029ea64a6a844))
* **mb:** resolve years from the release index first ([04c90dc](https://github.com/eightyeighteyes/aoty-chart-toy/commit/04c90dca0309a46ad88b95fab2446b3d14b49c31))
* **mb:** resolver core ([f237647](https://github.com/eightyeighteyes/aoty-chart-toy/commit/f2376475daab709d2c5681c41c8aba443f41475f))
* **packaging:** stamp each zip with its git SHA ([4659d4f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4659d4ffa2b05365252aa9585441dfa0d59301a7))
* sandbox environment for testing a branch before merge (AOTY-33) ([2f1f988](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2f1f988844e5b6b5e8195881ba4c896788ec6c7b))
* **scripts:** sandbox reset script ([df0238f](https://github.com/eightyeighteyes/aoty-chart-toy/commit/df0238f48bb70b38e398660c3c6a8a54529deab8))
* **sync:** Last.fm client and pure sync core ([876b12b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/876b12b9c602afaade843bf06c367335fa1c9f72))
* **sync:** Last.fm sync worker (AOTY-18) ([de8e21e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/de8e21efd7d107d10c4b4e6f74d98e28c7a7747d))
* **sync:** start the chart build and record Last.fm's name casing ([b6e1cf2](https://github.com/eightyeighteyes/aoty-chart-toy/commit/b6e1cf2f7cd85170361f110bd3ae13ef95220b81))
* **sync:** sync worker handler ([75803e1](https://github.com/eightyeighteyes/aoty-chart-toy/commit/75803e11db36eca0de76ce2b4430a30fa23e836e))
* **tools:** chart_check, the AOTY-21 acceptance diff ([6f004c4](https://github.com/eightyeighteyes/aoty-chart-toy/commit/6f004c4782110721ab6ee679d336dfbdac7566fb))
* **tools:** mb lane parity check against M0's caches ([33a7fed](https://github.com/eightyeighteyes/aoty-chart-toy/commit/33a7fedd0a3f3a6cd7c0a5a7bd311f4577d7f4e5))
* **tools:** score the sharded index against M0 ([bf8876b](https://github.com/eightyeighteyes/aoty-chart-toy/commit/bf8876bfc91a477403022a793096fd980de68c66))
* **tools:** sync_check acceptance tool ([c65371e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c65371e050fc24cd33d57613224429a7065607ab))
* **web:** user page shell ported from M0's renderer ([20c8174](https://github.com/eightyeighteyes/aoty-chart-toy/commit/20c817464a79a3e2d6d43aee918e4e7654f77904))


### Bug Fixes

* **chart:** serve Last.fm covers from the host kamp.fm's CSP allows ([4ea0d7c](https://github.com/eightyeighteyes/aoty-chart-toy/commit/4ea0d7cd67abe03602c6aa74d883ccf361a56571))
* **index:** size the S3 connection pool to the upload threads ([677b965](https://github.com/eightyeighteyes/aoty-chart-toy/commit/677b965f4cd3f1f2cdeec7ca70fa9094a1bd59f6))
* **infra:** create the ECS service-linked role in global ([e6b3210](https://github.com/eightyeighteyes/aoty-chart-toy/commit/e6b32102b3cf32bbd15d0d17eee2907362907949))
* **infra:** let deploy roles tag event source mappings ([94a378e](https://github.com/eightyeighteyes/aoty-chart-toy/commit/94a378e9e81c3c7383c5d93e1d839a5593912bec))
* **infra:** let deploy roles tag event source mappings ([2b4b094](https://github.com/eightyeighteyes/aoty-chart-toy/commit/2b4b094a84938314dd99a2d5ea9333c333d13878))
* **mb:** fill each batch despite short SQS receives ([c928a46](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c928a46dfb5c8841f77a55e4c4b44f14902fc3ea))
* **sync:** key punctuation-only albums and retry failures quickly ([c0dea13](https://github.com/eightyeighteyes/aoty-chart-toy/commit/c0dea1388da7bf2bbcae5954ff3e694a797612de))
* **sync:** key punctuation-only albums and retry failures quickly ([448f093](https://github.com/eightyeighteyes/aoty-chart-toy/commit/448f093a3492847cb98c6d848b975bb230c48296))
* **tools:** preview CSP allows archive.org, as kamp.fm will ([a8e549a](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a8e549a595ee28c07dc931e42f94d50d588ab37f))

## 1.0.0 (2026-11-22)


### Features

* add Lambda sources and a reproducible arm64 packager ([114849d](https://github.com/eightyeighteyes/aoty-chart-toy/commit/114849d78bef7764dc792ce77a3f51a866b8cee8))
* **infra:** let the deploy roles invalidate their shell on kamp.fm ([a49c6b5](https://github.com/eightyeighteyes/aoty-chart-toy/commit/a49c6b5e0038bb911b53ae378c913a36f4a86cf7))
