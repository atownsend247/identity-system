# Building a central login service

A series on building [identity-system](../README.md), a small login service that
sits in front of my self-hosted apps: forward-auth for the ones behind a reverse
proxy, and OpenID Connect for the ones that can't be. Each post is roughly a
20-30 minute read and covers one real stretch of the build, in order.

1. [One place to type a password](01-why-a-central-login-service.md)
2. [Shipping it: Jenkins, uv and a Debian container](02-shipping-it-jenkins-uv-and-a-container.md)
3. [An admin page and a proper frontend](03-an-admin-page-and-a-proper-frontend.md)
4. [Becoming an OpenID Connect provider](04-becoming-an-openid-connect-provider.md)
5. [Making Jenkins sign in](05-making-jenkins-sign-in.md)
6. [Moving configuration into the database](06-moving-configuration-into-the-database.md)
7. [Polish, and creating users from the browser](07-polish-and-creating-users-from-the-browser.md)
8. [A two-step login wizard](08-a-two-step-login-wizard.md)

The posts cover the history from the first commit on 28 September to 7 October.
Each post ends with a hidden HTML comment listing the commits it covers, so the
series can be checked against `git log`; blog-sync strips those before posting.
