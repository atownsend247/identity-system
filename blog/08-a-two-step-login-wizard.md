# Central login service: A two-step login wizard - Part 8

*~12 min read*

Part 7 closed with "that's the story to date". It wasn't quite. The login page still had one
awkward detail from the very first version: a single form with three fields, the last of them
labelled **"2FA code (only if you have it enabled)"**. Everyone, including people who have never
turned on two-factor, saw a box asking about something that didn't apply to them.

This post turns that form into a small wizard. Step one asks for an email and password. Step two,
a code from your authenticator app, appears only if your account has two-factor on. It's a small
change, but the interesting part is the constraint underneath it, because the obvious design
doesn't work.

## The obvious design, and why it doesn't work

The natural way to build this is a pre-flight check: when someone types their email, ask the
server "does this account have 2FA?" and show the code field if so. That's two requests, and
the first one is the problem.

An endpoint that answers "does `alex@example.com` have 2FA enabled?" before anyone has proved
who they are tells a stranger two things: that the account exists, and how well protected it is.
That's account enumeration with a bonus. The login endpoint is careful to answer a wrong email
and a wrong password identically (`incorrect email or password`), and a pre-flight check would
undo that on purpose.

So the question can only be asked *after* the password has been verified. That points at the
login call itself, and there I hit the second constraint.

## What sessionkit gives me

`AuthService.login(email, password, otp=None)` does everything in one go:

1. Look up the user and verify the password. Failure raises `AuthenticationError`.
2. If the user has confirmed TOTP, check the second factor. A missing code raises `OtpRequired`;
   a wrong one raises `OtpInvalid`.
3. Only then create the session and return the token.

There's no "password verified, code pending" state, and no half-logged-in token to carry from
step one to step two. By the rule from Part 1, I shouldn't reimplement password checking in the
route handler to get one: rules live in the library.

That turns out to be fine, because step 2 of `login` has a useful property. If the password is
right and the code is missing, the call fails with `OtpRequired` *before any session exists*. So
the server can learn "right password, needs a code" for free, and the only thing to fix is how
it says so.

## Making the signal machine-readable

Before this change, a password-only login for a 2FA user returned a `401` with the message
`enter the 6-digit code from your authenticator app`. A client could tell it apart from a wrong
password only by matching on that text, which is brittle. A wrong password is also a `401`,
and so is a locked account.

The fix is a flag in the response body:

```python
try:
    result = auth.login(
        payload.get("email", ""),
        payload.get("password", ""),
        otp=payload.get("otp") or None,
    )
except OtpRequired as exc:
    # The password was right but this account has 2FA on: no session
    # was created. The flag lets the login wizard move to its code
    # step without matching on the message text.
    return JSONResponse(
        status_code=401, content={"detail": str(exc), "otp_required": True}
    )
```

The status is still `401` and `detail` is still there, so everything that already calls
`/api/login` keeps working. The flag appears only on this one case. A wrong password has no
`otp_required`, and neither does a wrong code (which stays a `422`, as `OtpInvalid` always has;
see the `_ERROR_STATUS` note in Part 3).

One thing this does leak, and it's worth being plain about it: anyone who knows the correct
password for an account can now learn that it has 2FA. That's acceptable. Someone with the
password could learn it by getting the "enter your code" message before, and they've already
cleared the harder bar.

## The wizard

On the frontend, `ApiError` gained a `body` so a caller can read more than the message:

```ts
export class ApiError extends Error {
  status: number
  body: Record<string, unknown>

  constructor(status: number, message: string, body: Record<string, unknown> = {}) {
    super(message)
    this.status = status
    this.body = body
  }
}
```

`Login.tsx` then holds a `step` of `'credentials'` or `'otp'`. Step one posts `{ email, password,
rd }`. There are three outcomes:

- **Success** (no 2FA on the account): navigate to the server's `redirect_to`, as before.
- **`otp_required`**: switch to the code step. No error banner, because nothing went wrong.
- **Anything else**: show the message and stay on step one.

```tsx
} catch (err) {
  if (step === 'credentials' && err instanceof ApiError && err.body.otp_required === true) {
    setStep('otp')
    setSubmitting(false)
    return
  }
  setError(err instanceof ApiError ? err.message : 'Something went wrong')
  setSubmitting(false)
}
```

Step two shows only the code field, with `autoComplete="one-time-code"` so phones can offer the
code from a text message or password manager, and a line saying a recovery code works too. It
posts `{ email, password, otp, rd }`. A wrong code shows its error and stays put so the user can
retry. A **Back** button returns to step one and clears the code and any error.

### The password has to stay in memory

Because the server keeps no pending state, step two must send the password again. It lives in
the component's React state between the steps and goes nowhere else: not `localStorage`, not
the URL. When the page navigates away or reloads, it's gone, and the user starts at step one.
That's a small cost for not building a server-side "pending login" store that the library
doesn't have.

### The redirect rule didn't move

The open-redirect guard from Part 1 is untouched. Both steps send the raw `rd`, the server
decides whether it's trusted, and the browser navigates to whatever `redirect_to` comes back,
never to `?rd=` directly. A two-step flow is exactly where it would be easy to let step two
"remember" the `rd` itself and skip that check, so a test keeps both honest.

## Tests

On the backend, the existing 2FA test grew three assertions. A password-only login gets
`otp_required: true` and no cookie; a wrong code gets a `422` without the flag; a valid code
logs in. The wrong-password test now checks the flag is absent.

On the frontend there are three new cases:

1. A user without 2FA signs in from step one and the code field never appears.
2. A 2FA user reaches step two with no error showing, enters a wrong code (error, still on step
   two), then a right one, and the final request contains the email, password *and* code.
3. **Back** returns to the password form and the code field is gone.

## What I'd still change

**A real pending-login state, in the library.** Re-sending the password works but it's a second
password check and a second round of Argon2. If sessionkit grew a "password verified" token with
a short lifetime, the second step wouldn't need the password at all. It belongs there, with a
release, not here.

**Rate limiting on the password step.** The lockout protects the six-digit code, but nothing
slows down guessing at step one. That gap has been documented since Part 1, and the wizard doesn't
change it.

---

That's the login page doing what its label always claimed. The code field is now there only for
the people it's for.

<!-- commits: pending -->

Previous: [← Polish, and creating users from the browser](07-polish-and-creating-users-from-the-browser.md)
