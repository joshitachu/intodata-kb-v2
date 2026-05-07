# EP-002 - Company-Local App Access

This feature is deployment-local: one company environment has its own users,
access requests, invites, SMTP settings, and superadmins. Request Access means
requesting access to this deployed customer app, not direct backend/API access
and not a global IntoData customer portal.

Flow:

1. A visitor submits Request Access from `/login`.
2. A company superadmin reviews the request in Admin > Access.
3. Approval creates a single-use invite with a 48-hour expiry and sends an
   `/onboarding?token=...` link by the deployment SMTP settings. In development,
   missing SMTP logs the link instead.
4. The invitee completes username, password, full name, role/title, and intended
   platform use.
5. The app creates a normal user, stores the questionnaire answers on the invite,
   creates onboarding state, invalidates the token, signs the user in, and sends
   them to `/settings/security`.
6. MFA remains mandatory. New users cannot use normal app features until TOTP is
   enabled and the 10 single-use backup codes have been shown once.

Future customer onboarding remains a deployment-track concern: IntoData deploys
a separate company environment, then that company's admin manages app users
locally.
