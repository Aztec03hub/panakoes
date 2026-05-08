// Static-adapter prerendering: the admin SPA ships as static files on
// S3 + CloudFront. We pre-render the entry shell; client-side routing handles
// authenticated views once Better-Auth lands.
export const prerender = true;
export const ssr = false;
export const trailingSlash = "ignore";
