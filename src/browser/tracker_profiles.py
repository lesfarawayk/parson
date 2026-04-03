"""
Tracker profiles — site-specific selectors and URLs for each supported tracker.

Both Rutracker and Pornolab run on TorrentPier (phpBB fork), so the structure
is very similar. Differences are mainly in URLs and minor selector variations.
"""


class TrackerProfile:
    """Configuration for a specific tracker site."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


PROFILES = {
    "rutracker": TrackerProfile(
        name="Rutracker",
        base_url="https://rutracker.org",
        forum_url="https://rutracker.org/forum/viewforum.php",

        # Login
        login_url="/forum/login.php",
        login_user_sel="input[name='login_username'], #login-form-login-user-name",
        login_pass_sel="input[name='login_password'], #login-form-login-password",
        login_submit_sel="input[name='login'], #login-form-submit",

        # Registration
        register_url="/forum/profile.php?mode=register",
        reg_agree_sel="input[name='agree'], #rules-btn, a.agree",
        reg_user_sel="input[name='username'], #username",
        reg_pass_sel="input[name='new_password'], #new_password",
        reg_pass_confirm_sel="input[name='password_confirm'], #password_confirm",
        reg_email_sel="input[name='email'], #email",
        reg_captcha_img_sel="img[src*='captcha'], .captcha-img, #cap_img",
        reg_captcha_input_sel="input[name='cap_code'], input[name='captcha']",
        reg_submit_sel="input[type='submit'][name='submit'], button[type='submit']",

        # Category / topic listing
        topics_per_page=50,
        topic_row_sel="tr.hl-tr, tr.t-row",
        topic_link_sel="a.torTopic, a.tt-text, td.t-title-col a, td.t-title a",
        topic_id_pattern=r"t=(\d+)",

        # Topic details
        post_body_sel=".post_body, #topic_main .post_wrap .post_body",
        cover_img_sel="img, var.postImg",

        # Download
        download_url_tpl="/forum/dl.php?t={topic_id}",
    ),

    "pornolab": TrackerProfile(
        name="Pornolab",
        base_url="https://pornolab.net",
        forum_url="https://pornolab.net/forum/viewforum.php",

        # Login
        login_url="/forum/login.php",
        login_user_sel="input[name='login_username'], #login-form-login-user-name",
        login_pass_sel="input[name='login_password'], #login-form-login-password",
        login_submit_sel="input[name='login'], #login-form-submit",

        # Registration
        register_url="/forum/profile.php?mode=register",
        reg_agree_sel="input[name='agree'], input[name='agreed'], input[value*='согласен']",
        reg_user_sel="input[name='username']",
        reg_pass_sel="input[name='new_password'], input[name='cur_password']",
        reg_pass_confirm_sel="input[name='password_confirm']",
        reg_email_sel="input[name='email']",
        reg_captcha_img_sel="img[src*='captcha'], img.captcha-img, #cap_img, img[id*='cap']",
        reg_captcha_input_sel="input[name='cap_code'], input[name='captcha_code'], input[name='captcha']",
        reg_submit_sel="input[type='submit'][name='submit'], input[type='submit'], button[type='submit']",

        # Category / topic listing
        topics_per_page=50,
        topic_row_sel="tr.hl-tr, tr.t-row, tr.tCenter",
        topic_link_sel="a.torTopic, a.tt-text, td.t-title-col a, td.t-title a, a.tLink",
        topic_id_pattern=r"t=(\d+)",

        # Topic details
        post_body_sel=".post_body, #topic_main .post_wrap .post_body, .post_wrap .post_body",
        cover_img_sel="img, var.postImg",

        # Download
        download_url_tpl="/forum/dl.php?t={topic_id}",
    ),
}


def get_profile(name: str) -> TrackerProfile:
    """Get tracker profile by name. Raises KeyError if not found."""
    if name not in PROFILES:
        raise KeyError(f"Unknown tracker: {name}. Available: {list(PROFILES.keys())}")
    return PROFILES[name]


def list_profiles() -> list[str]:
    return list(PROFILES.keys())
