# Changelog

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
