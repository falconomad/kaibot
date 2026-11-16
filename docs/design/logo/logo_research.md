# What makes a logo work, and what that means for kaibo

Issue #255 · 16 Nov 2026 · Design. The research behind the six concepts in [`logo_concepts.md`](logo_concepts.md).

## How this was done

- **Read:** published accounts of well-known marks, and the brand pages of three market apps. Links are at the end.
- **Described, never reproduced.** Other brands' marks are described in words only. No logo, glyph, wordmark or
  asset of another brand was copied, traced or imitated, and none is in this repository.
- **Two kinds of statement.** A statement with a link comes from that source. A statement marked *(my reading)* is my
  own judgement of the field, not a sourced fact.

## The short version

| # | Principle | The test | What it means for kaibo |
|---|---|---|---|
| 1 | One idea | Can you say it in one sentence? | Each concept has one sentence. A second idea is cut, not added. |
| 2 | A distinct silhouette | Seen once or twice, can you sketch it from memory? | Judge the mark filled in one colour, small. |
| 3 | Simple geometric construction | Can you rebuild it from circles and straight lines? | Every concept is at most four parts. The generator is the proof. |
| 4 | Legible at 16 px | Does a browser tab still show it? | Strokes of about 2 px at 16 px. Each sheet shows the real pixels. |
| 5 | Works in one colour | Does the idea survive in black only? | Every concept ships a one-colour file. Two lose part of their idea; we say which. |
| 6 | An ownable colour | Would you know the brand from the colour? | Blue, because the app already uses it and the other colours mean something. |
| 7 | Wordmark and symbol work apart | Does the icon need the name? Does the name need the icon? | The header needs the word; the app icon and tab need a symbol. |
| 8 | Motion potential | Is there a natural way for it to arrive? | Once, under a second, then still. Nothing moves with "reduce motion" on. |

## The eight principles

### 1. One idea

A mark that lasts says one thing. Nike's Swoosh was drawn in 1971 by Carolyn Davidson, then a design student, after
Phil Knight asked for a mark that conveys motion ([Wikipedia](https://en.wikipedia.org/wiki/Swoosh)). Deutsche Bank's
mark, by Anton Stankowski, is a slash inside a square. It beat 140 other entries in a competition and first appeared in
1974; the bank says the slash came to be read as growth within a secure setting
([Deutsche Bank](https://www.db.com/news/detail/20240422-slash-in-a-square-deutsche-bank-logo-turns-50?language_id=1)).
One line and one square carry one thought.

**For kaibo:** the product has one honest thought to offer: follow the market, with smaller falls, and protect the
pot first. A concept may show the line, the protection or the calm. Not all three.

### 2. A distinct silhouette

The designer Sagi Haviv gives three criteria for a good logo: appropriate, distinctive and memorable, and simple. His test for the second is whether you could describe or sketch the mark after seeing it once or
twice ([summary](https://medium.com/designorex/the-3-rules-of-good-logo-design-9ccc05567310)). Mastercard's two
interlocking circles pass it so well that the company dropped its name from the mark in 2019, citing research in which
more than 80 percent recognised the symbol without the word
([Dezeen](https://www.dezeen.com/2019/01/10/mastercard-rebrand-pentagram-design/)). Twitter did the same in 2012: the
bird alone, no wordmark
([The Next Web](https://thenextweb.com/news/twitter-changes-its-logo-to-a-simplified-twitterbird-crafted-out-of-overlapping-circles)).

**For kaibo:** nobody knows kaibo yet, so the symbol cannot stand alone for years. The silhouette still has to be
unlike the things around it on a phone: in one colour, at icon size.

### 3. Simple geometric construction

The 2012 Twitter bird was made from overlapping circles (same source). Mastercard is two circles. Deutsche Bank is a
line and a square. Coinbase's symbol is described by its designers as sitting between a C and a circle, a small step
from the app icon it replaced ([It's Nice That](https://www.itsnicethat.com/news/moniker-coinbase-rebrand-graphic-design-250322)).
Shapes like these survive bad printing, small sizes and other people's redrawing.

**For kaibo:** every concept is built from straight strokes, circles and at most one sine wave, on a fixed stroke
width. The script that draws them (`gen/geo.py`, `gen/build.py`) is the construction drawing.

### 4. Legible at 16 px

Haviv's third criterion, simple, comes with a test: the mark must still be recognisable at very small sizes, such as a
favicon (same summary). On a phone the logo is small almost everywhere: 16 px in a tab, 26 px in the header, 60 px on
the home screen.

**For kaibo:** the thinnest stroke should stay near 2 px at 16 px. The proof sheets show each favicon at its real
size and enlarged pixel by pixel.

### 5. Works in one colour

Brand kits in this field set the logo in one colour first. Kalshi's kit gives the logo in black or white, with green
allowed only on named backgrounds ([Kalshi brand kit](https://kalshi.com/brandkit)). Polymarket's brand page says it
prefers the monochrome versions ([Polymarket brand](https://polymarket.com/brand)). A mark that needs two colours to
make sense fails on a stamp, an invoice, or a dark-mode icon that the system tints.

**For kaibo:** each concept has a one-colour file. Where the idea lives in the second colour, the concept page says so.

### 6. An ownable colour

- **Klarna** kept its pink through a 2024 redraw of the wordmark
  ([Creative Review](https://creativereview.co.uk/klarna-brand-refresh),
  [Design Your Way](https://www.designyourway.net/blog/klarna-logo/)).
- **Robinhood** moved to black, white and neutrals with one electric yellow-green of its own
  ([Porto Rocha](https://www.portorocha.com/robinhood)).
- **Kalshi** uses two greens, one for light and one for dark backgrounds (brand kit above).
- **Polymarket** names its blue and gives its value (brand page above).
- **Coinbase** brightened the blue it already had (It's Nice That, above).
- **Stripe** is known by a blue-violet and **Wise** by a flag symbol
  ([Ebaq Design](https://www.ebaqdesign.com/blog/fintech-logos)).

**For kaibo:** the app already has a colour system. Green means gain, red means loss or a halt, amber means paper or
attention, violet means the agent. A logo in any of those would say something untrue. That leaves the accent blue,
which the code already calls `--sea`. Blue is common in finance *(my reading)*, so the colour alone will not be
kaibo's. The pairing can be: near-black, off-white and this one blue, used the same way everywhere.

### 7. Wordmark and symbol that also work apart

Some brands are known by a symbol alone (Nike, Mastercard). Others have no symbol: Stripe and Chime are wordmarks
(Ebaq Design, above), and Kalshi's kit shows a wordmark only. Both work. What fails is a symbol that means nothing
without the name next to it, in a place where the name does not fit.

**For kaibo:** the header has room for the word. The app icon, the tab and the home screen do not. So each concept
delivers a symbol and a wordmark, and the pages say which one leads.

### 8. Motion potential

The Swoosh was asked to convey motion while standing still. Coinbase's identity uses motion that is subtle in the
product and livelier in marketing (It's Nice That, above). A mark built from a few clean parts has an obvious way to
arrive: its parts, in order.

**For kaibo:** the v4 motion rules already say how: short, calm, only position and opacity, and nothing at all under
"reduce motion". The logo arrives once and then stays still. A logo that keeps moving would fight the one thing in the
header that should move: the engine's live light.

## How the field does it

Described from the linked sources. This is a reading list, not a ranking.

| Brand | Field | Kind of mark | Colour | The idea, as the source puts it |
|---|---|---|---|---|
| Deutsche Bank | Bank | Symbol: a slash in a square | | Read as growth in a secure setting; unchanged for fifty years |
| Mastercard | Payments | Symbol: two interlocking circles | Red and yellow | So well known that the name was dropped in 2019 |
| Robinhood | Trading app | Symbol (a feather) and wordmark | Black, white, one neon accent | The feather was simplified to bring out the arrow in it; "less is more" in a crowded field |
| Coinbase | Crypto exchange | Symbol between a C and a circle, and wordmark | A brightened blue | Simple and familiar, to earn trust from newcomers |
| Stripe | Payments | Wordmark only | Blue-violet | Professional; angled cuts on two letters |
| Wise | Money transfer | Symbol (a flag) and wordmark | Blue, in this source | Money without borders |
| Klarna | Payments | Wordmark; a single K as app icon | Pink | The 2017 wordmark ended in a full stop; the 2024 redraw removed it |
| Kalshi | Prediction market | Wordmark only | Green, two shades | Clear space is measured from the letter K |
| Polymarket | Prediction market | Icon and wordmark | A named blue | Monochrome versions preferred |
| Calibra, Current | Wallet, bank app | Calibra: a tilde in a circle | | In 2019 Calibra's mark was said to look much like Current's ([The Verge, mirrored](https://thevergetoday.pages.dev/2019/6/20/18692621/facebook-calibra-current-bank-logo-similarity)) |
| Equals | Payments | Two flattened ovals, like an equals sign | Raspberry on plum | ([Logos World](https://logos-world.net/equals-unveils-new-logo-and-brand-identity/)) |
| Solitone | Trading firm | Two wave fragments | Cornflower blue | A wave that keeps its shape ([Abduzeedo](https://abduzeedo.com/node/88813)) |

## What the field overuses *(my reading)*

- **Pictures of profit:** candlesticks, bulls, rockets, arrows pointing up and to the right. They promise a result.
  kaibo must not.
- **Pictures of a machine:** robot heads, circuit lines, sparkles. The old name ended in "bot"; the new one does not.
- **Pictures of safety:** shields, locks, umbrellas. Borrowed from insurance and antivirus.
- **Blue, and the letter K.** Several finance brands start with K (Klarna, Kraken, KuCoin, Kalshi). A K mark for
  kaibo has to look unlike theirs.

## What is open for kaibo *(my reading)*

- **Calm.** Most trading brands sell speed. kaibo's promise is the opposite: smaller falls.
- **The sea.** *Kai* is the Hawaiian word for the sea, and one Japanese reading of the characters for ocean (海) and
  shell (貝) ([Wikipedia](https://en.wikipedia.org/wiki/Kai_(name))). In Japanese, *kaibō* written 海防 means coastal
  defence ([dictionary](https://www.nihongomaster.com/japanese/dictionary/word/73879/kaibou-%E6%B5%B7%E9%98%B2-%E3%81%8B%E3%81%84%E3%81%BC%E3%81%86)).
  The name check lists a less welcome reading too.
- **The app's own signs.** The pot's line on Home ends in a point. The engine's state is a small live light. Each
  position sits on a track between its stop and its target. These are true to the product and nobody else has them.

## The checks every concept went through

1. One sentence for the idea.
2. Built from at most four simple parts, on one stroke width.
3. Drawn as outlines only: no live text, no font needed to open the file.
4. Shown at 16, 32, 60 and 180 px, in the v4 header, on dark and on light, and in one colour.
5. A lookalike check: which known marks it could be confused with, and why it is or is not too close. Concepts that
   were too close were dropped; the list is in `logo_concepts.md`.
6. No promise of profit in the picture or the words.

**What this is not:** a trademark search. Nobody here checked registers or ran an image search. That has to happen
before any of these is used in public.

## Sources

- Sagi Haviv's three criteria, summarised: <https://medium.com/designorex/the-3-rules-of-good-logo-design-9ccc05567310>
- Deutsche Bank, "Slash in a square: Deutsche Bank logo turns 50": <https://www.db.com/news/detail/20240422-slash-in-a-square-deutsche-bank-logo-turns-50?language_id=1>
- Dezeen, Mastercard drops its name from its logo: <https://www.dezeen.com/2019/01/10/mastercard-rebrand-pentagram-design/>
- The Next Web, Twitter's 2012 bird: <https://thenextweb.com/news/twitter-changes-its-logo-to-a-simplified-twitterbird-crafted-out-of-overlapping-circles>
- Wikipedia, Swoosh: <https://en.wikipedia.org/wiki/Swoosh>
- Porto Rocha, Robinhood: <https://www.portorocha.com/robinhood>
- It's Nice That, Coinbase: <https://www.itsnicethat.com/news/moniker-coinbase-rebrand-graphic-design-250322>
- Ebaq Design, fintech logos: <https://www.ebaqdesign.com/blog/fintech-logos>
- Creative Review, Klarna: <https://creativereview.co.uk/klarna-brand-refresh>; Design Your Way, Klarna logo: <https://www.designyourway.net/blog/klarna-logo/>
- Kalshi brand kit: <https://kalshi.com/brandkit>; Polymarket brand: <https://polymarket.com/brand>
- Calibra and Current: <https://thevergetoday.pages.dev/2019/6/20/18692621/facebook-calibra-current-bank-logo-similarity>
- Equals: <https://logos-world.net/equals-unveils-new-logo-and-brand-identity/>; Solitone: <https://abduzeedo.com/node/88813>
- Wikipedia, Kai (name): <https://en.wikipedia.org/wiki/Kai_(name)>
