# SOFR Trade String Syntax Reference

## Overview

The trade string is a compact, space-delimited format used to describe SOFR
options and futures trades in a single line. The parser converts the string
into structured leg data for confirmation, card generation, and exchange
reporting.

## Basic Format

```
<contract_code> <option_type> <strikes> <strategy> <price_format>
```

## Price Format (determines direction)

| Format       | Direction | Example    | Meaning               |
|--------------|-----------|------------|-----------------------|
| `price/qty`  | BUY       | `4/500`    | Buy 500 at 4 ticks    |
| `qty@price`  | SELL      | `500@4`    | Sell 500 at 4 ticks   |

Prices are in ticks (1 tick = 0.01 = 1 basis point).

## Contract Codes

| Format     | Example  | Description                     |
|------------|----------|---------------------------------|
| `SFRxy`    | `SFRH6`  | Quarterly SOFR future/option    |
| `SR3xy`    | `SR3H6`  | Quarterly SOFR (alternate)      |
| `0Qxy`     | `0QZ5`   | Short-dated SOFR (S0 pack)      |
| `2Qxy`     | `2QM6`   | Short-dated SOFR (S2 pack)      |
| `3Qxy`     | `3QU6`   | Short-dated SOFR (S3 pack)      |

Where `x` = month code (F,G,H,J,K,M,N,Q,U,V,X,Z) and `y` = year digit.

## Option Types

- `C` or `CALL` — Call option
- `P` or `PUT` — Put option

## Strategy Keywords

| Keyword(s)                    | Strategy         | Strikes |
|-------------------------------|------------------|---------|
| `CS`, `CALLSPREAD`, `CSPD`   | Call spread      | 2       |
| `PS`, `PUTSPREAD`, `PSPD`    | Put spread       | 2       |
| `^`                           | Straddle         | 1       |
| `^^`                          | Strangle         | 2       |
| `RR`, `RISKREV`              | Risk reversal    | 2       |
| `C FLY`, `BFLYC`             | Call butterfly   | 3       |
| `P FLY`, `BFLYP`             | Put butterfly    | 3       |
| `TREE`, `CTREE`, `XMAS`      | Call xmas tree   | 3       |
| `PTREE`, `PUTTREE`           | Put xmas tree    | 3       |
| `C CON`, `CONDORC`           | Call condor      | 4       |
| `P CON`, `CONDORP`           | Put condor       | 4       |
| `IC`, `IRON CONDOR`          | Iron condor      | 4       |
| `IRON FLY`                   | Iron butterfly   | 3       |

## Ratio Spreads

- `1X2`, `1BY2` — 1:2 ratio
- `1X3`, `1BY3` — 1:3 ratio
- `2X3`, `2BY3` — 2:3 ratio

## CVD (Covered / Delta Hedge)

```
CVD <futures_price> D <delta_percent>
```

Example: `CVD 95.50 D 40` — hedge at 95.50, 40% delta

Override futures side: `CVD 95.50(+)` = force buy, `CVD 95.50(-)` = force sell

## VS Trades (Two Legs)

```
<leg1> VS <leg2> <price_format>
```

Example: `SFRH6 C 96.00 VS SFRM6 C 96.25 4/500`

The first leg takes the direction from the price format; the second leg
takes the opposite.

## Bracket Wrapper

```
[<segment1>, <segment2>] <price_format>
```

Groups multiple segments under one shared price and direction.

## Direction Overrides

- `(+)` after a strike = force BUY for that strike
- `(-)` after a strike = force SELL for that strike
- `(CALLS)` = mark strategy as call-centric
- `(PUTS)` = mark strategy as put-centric

## Contract Override

Parentheses around a non-numeric token override the contract code:

```
SFRH6 SFRM6 C 96.00 CS (SFRH6) 4/500
```

This forces the trade to use only the SFRH6 contract.

## Examples

```
SFRH6 C 96.00 96.25 CS 4/500              Call spread
SFRH6 C 96.00 96.25 96.50 C FLY 2/200     Call butterfly
SFRH6 95.75 ^ 3/100                        Straddle
SFRH6 95.50 95.75 ^^ 2/300                 Strangle
SFRH6 C 96.00 3/500 CVD 95.50 D 40        Call with CVD
SFRH6 C 96.00 VS SFRM6 C 96.25 4/500      Calendar spread
[SFRH6 C 96.00, SFRM6 P 95.50] 4/500      Bracket wrapper
500@4 SFRH6 P 95.75 96.00 PS               Put spread (sell)
```
