"""The site menu's shared entry tag and account partial (#338 item 28)."""

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from common.test_helpers import mark_gate_passed

ARENA_HOST = "melee.example.com"


def render_link(tag_arguments: str, **context) -> str:
    template = Template("{% load menu %}{% menu_link " + tag_arguments + " %}")
    return template.render(Context(context))


class MenuLinkTagTest(SimpleTestCase):
    """{% menu_link %} renders the two anchor shapes base.html's menu uses."""

    def test_a_submenu_entry_off_its_page(self):
        html = render_link("'battle:list' 'Battles' page='battles'", active_page="home")

        self.assertEqual(
            html, f'<a href="{reverse("battle:list")}" class="submenu">Battles</a>'
        )

    def test_a_submenu_entry_on_its_page_is_disabled(self):
        html = render_link(
            "'battle:list' 'Battles' page='battles'", active_page="battles"
        )

        self.assertEqual(
            html,
            f'<a href="{reverse("battle:list")}" class="submenu disabled">Battles</a>',
        )

    def test_an_entry_without_a_page_is_never_marked(self):
        html = render_link("'admin:index' 'Django Admin'", active_page="")

        self.assertEqual(
            html, f'<a href="{reverse("admin:index")}" class="submenu">Django Admin</a>'
        )

    def test_an_account_entry_has_no_class_unless_current(self):
        off = render_link(
            "'login' 'Login' page='login' submenu=False", active_page="home"
        )
        on = render_link(
            "'login' 'Login' page='login' submenu=False", active_page="login"
        )

        self.assertEqual(off, f'<a href="{reverse("login")}">Login</a>')
        self.assertEqual(on, f'<a href="{reverse("login")}" class="disabled">Login</a>')

    def test_a_primary_key_reaches_the_url(self):
        html = render_link("'characters:edit' 'Character Sheet' pk=7")

        self.assertIn(f'href="{reverse("characters:edit", args=[7])}"', html)

    def test_the_label_is_escaped(self):
        html = render_link("'profile' label submenu=False", label="<b>eve</b> Profile")

        self.assertIn("&lt;b&gt;eve&lt;/b&gt; Profile", html)


@override_settings(
    ALLOWED_HOSTS=["testserver", ARENA_HOST],
    THEME_HOSTS={ARENA_HOST: "classic-arena"},
)
class MenuAccountPartialTest(TestCase):
    """Both theme branches share the account links, each with its own extras."""

    def menu_of(self, response) -> str:
        html = response.content.decode()
        start = html.index('<nav class="menu" id="menu">')
        return html[start : html.index("</nav>", start)]

    def admin(self):
        user = get_user_model().objects.create_user(
            username="menu-admin", password="menu-admin-pass-338"
        )
        user.is_role_admin = True
        user.save()
        self.client.force_login(user)
        mark_gate_passed(self.client)
        return user

    def test_the_studio_admin_menu_has_the_generators_and_no_studio_link(self):
        self.admin()

        menu = self.menu_of(self.client.get(reverse("battle:list")))

        self.assertIn(">Ruin Generator</a>", menu)
        self.assertIn(">Django Admin</a>", menu)
        self.assertNotIn(">Tarmar Studio</a>", menu)
        self.assertIn('class="submenu disabled">Battles</a>', menu)
        self.assertIn(">menu-admin Profile</a>", menu)

    def test_the_arena_admin_menu_links_back_to_the_studio_without_generators(self):
        self.admin()

        menu = self.menu_of(
            self.client.get(reverse("battle:list"), HTTP_HOST=ARENA_HOST)
        )

        self.assertIn(">Tarmar Studio</a>", menu)
        self.assertIn(">Users</a>", menu)
        self.assertNotIn(">Ruin Generator</a>", menu)
        self.assertNotIn(">Home</a>", menu)

    def test_a_guest_is_offered_register_and_login_on_both_doors(self):
        mark_gate_passed(self.client)
        for host in ("testserver", ARENA_HOST):
            with self.subTest(host=host):
                menu = self.menu_of(
                    self.client.get(reverse("battle:list"), HTTP_HOST=host)
                )

                self.assertIn(f'<a href="{reverse("register")}">Register</a>', menu)
                self.assertIn(f'<a href="{reverse("login")}">Login</a>', menu)
                self.assertNotIn("Logout", menu)
