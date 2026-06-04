# amnesia-adsmaster-test

Test fixture repo for the Amnesia **Adsmaster** git-sync → reconcile flow.

`campaigns/google/adsmaster-smoke/campaign.yml` declares the *desired state* of a
Google Ads campaign. On push, a GitHub webhook tells the Amnesia backend to pull +
parse this repo, which materializes the campaign so it appears in the Adsmaster
"Run sync" picker on web-staging.amnesia.com.
