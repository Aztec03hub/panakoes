/**
 * Verification-email sender.
 *
 * Production path: hand-built SES `SendEmailCommand` against the verified
 * sender identity `noreply@lafayettelabs.com`, with `phil@lafayettelabs.com`
 * as the reply-to so a bounced or human-replied verification email lands in
 * Phil's inbox rather than dead-lettering at noreply.
 *
 * The sender is exposed as a `VerificationEmailSender` interface so tests
 * inject an in-memory capturing implementation. Production code builds the
 * SES-backed sender from config; the auth service does NOT call SES through
 * the notification service for this flow because the verification email is
 * synchronous-best-effort on sign-up (no DynamoDB hop, no retry queue) and
 * has its own IAM scope on the auth task role.
 *
 * SES deliverability dependency: the `lafayettelabs.com` domain is still
 * PENDING DKIM verification at the time of this PR (see PR #265). The
 * verified single-address sender path works today; the domain-wide MAIL FROM
 * + DKIM signature lands once the Cloudflare DNS records propagate.
 */
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";

export interface VerificationEmail {
  to: string;
  verifyUrl: string;
}

export interface VerificationEmailSender {
  send(email: VerificationEmail): Promise<void>;
}

export interface SesSenderConfig {
  region: string;
  fromAddress: string;
  replyToAddress: string;
}

/**
 * Compose the plaintext + HTML bodies for a verification email. Kept as a
 * pure function so tests can assert on the exact rendered output without
 * spinning up SES.
 */
export function renderVerificationEmail(verifyUrl: string): {
  subject: string;
  text: string;
  html: string;
} {
  const subject = "Verify your Panakoes email address";
  const text = [
    "Welcome to Panakoes.",
    "",
    "Click the link below to verify your email address. The link expires in 1 hour.",
    "",
    verifyUrl,
    "",
    "If you did not create this account, you can ignore this email.",
  ].join("\n");
  const html = [
    "<!doctype html>",
    '<html><body style="font-family: system-ui, sans-serif; max-width: 560px; margin: 2rem auto; line-height: 1.5;">',
    '<h2 style="margin-bottom: 0.5rem;">Welcome to Panakoes</h2>',
    "<p>Click the button below to verify your email address. The link expires in 1 hour.</p>",
    `<p><a href="${verifyUrl}" style="display:inline-block;padding:0.75rem 1.25rem;background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px;">Verify email</a></p>`,
    `<p style="color:#666;font-size:0.875rem;">Or paste this URL into your browser: <br /><code>${verifyUrl}</code></p>`,
    '<p style="color:#666;font-size:0.875rem;">If you did not create this account, you can ignore this email.</p>',
    "</body></html>",
  ].join("");
  return { subject, text, html };
}

/* c8 ignore start -- requires real AWS SES credentials; exercised via integration deploy + the dev-environment verification flow rather than unit tests */
/**
 * Build a real-SES-backed sender. Construction is lazy (no AWS calls) so
 * import is safe in test contexts that never reach `send()`.
 */
export function createSesEmailSender(config: SesSenderConfig): VerificationEmailSender {
  const client = new SESClient({ region: config.region });
  return {
    async send(email: VerificationEmail): Promise<void> {
      const { subject, text, html } = renderVerificationEmail(email.verifyUrl);
      await client.send(
        new SendEmailCommand({
          Source: config.fromAddress,
          ReplyToAddresses: [config.replyToAddress],
          Destination: { ToAddresses: [email.to] },
          Message: {
            Subject: { Data: subject, Charset: "UTF-8" },
            Body: {
              Text: { Data: text, Charset: "UTF-8" },
              Html: { Data: html, Charset: "UTF-8" },
            },
          },
        }),
      );
    },
  };
}

/* c8 ignore stop */

/**
 * In-memory capturing sender. Used in tests and as the default when the
 * service boots without SES credentials (e.g. local dev without AWS access).
 */
export function createInMemoryEmailSender(): VerificationEmailSender & {
  sent: VerificationEmail[];
} {
  const sent: VerificationEmail[] = [];
  return {
    sent,
    async send(email: VerificationEmail): Promise<void> {
      sent.push(email);
    },
  };
}
