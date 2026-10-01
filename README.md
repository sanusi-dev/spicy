# Spicy

Spicy is a lightweight web-based POS for restaurant order management. Cashiers enter all orders and payments at the counter; waiters use physical dockets and do not access the system. It supports dine-in, delivery, takeaway, and related order types.

## Documentation

The reverse-engineered, living technical documentation for the entire project is indexed at [`docs/README.md`](docs/README.md). Start there for architecture, cross-app dependencies, POS execution flows, database behavior, and troubleshooting guidance.

## Landing page

The marketing site lives in its own repo — **[sanusi-dev/spicy-landing](https://github.com/sanusi-dev/spicy-landing)** — and deploys to Netlify independently. It is plain HTML/CSS/JS with no build step and no runtime dependency on this codebase.

The coupling is on **content, not code**: the landing page must stay true to
[`FEATURES.md`](FEATURES.md). When a feature ships, is deferred or changes
behaviour, update the landing page in the same breath. The claims it makes that
are easiest to get out of sync:

| Landing page says | Backed by |
|---|---|
| Cashiers take every order and payment; waiters use paper dockets | Scope, `FEATURES.md` |
| Food and drinks tracked and reported apart | §C Departmental split |
| Shift opens with a float, closes against a counted drawer | §A5 Shift management |
| Stock values itself at weighted-average cost | §A3, feature 14 |
| Recipe cards compared against actual kitchen usage | §A3, feature 16b |
| Daily P&L splits food, drinks and total | §A10, feature 62 |
| Three staff roles: admin, manager, cashier | §A1, feature 3 |
| "Local or Cloud. Your Choice." | §D Architecture constraints |

Do **not** advertise these as shipped — they are Planned or Deferred in
`FEATURES.md`: the local print agent (printer config exists, the agent does
not), customer master / loyalty, discounts and coupons, and multi-branch.

## Quickstart

### Prerequisites

To run the app in the recommended configuration, you will need the following installed:
- [Docker](https://www.docker.com/get-started) and [Docker Compose](https://docs.docker.com/compose/install)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (for Python)
- [node and npm](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm) (for JavaScript)

On Windows, you will also need to install `make`, which you can do by
[following these instructions](https://stackoverflow.com/a/57042516/8207).

### Initial setup

Run the following command to initialize your application:

```bash
make init
```

This will:

- Build and run your Postgres database
- Build and run your Redis database
- Run your database migrations
- Install front end dependencies

Then you can start the app:

```bash
make dev
```

This will run your Django server and build and run your front end (JavaScript and CSS) pipeline.

Your app should now be running! You can open it at [localhost:8000](http://localhost:8000/).

If you're just getting started, [try these steps next](https://docs.saaspegasus.com/getting-started/#post-installation-steps).

## Using the Makefile

You can run `make` to see other helper functions, and you can view the source
of the file in case you need to run any specific commands.

## Installation - Native

You can also install/run the app directly on your OS using the instructions below.

You can setup a virtual environment and install dependencies in a single command with:

```bash
uv sync
```

This will create your virtual environment in the `.venv` directory of your project root.

## Set up database

*If you are using Docker you can skip these steps.*

Create a database named `spicy`.

```
createdb spicy
```

Create database migrations:

```
uv run manage.py makemigrations
```

Create database tables:

```
uv run manage.py migrate
```

## Running server

```bash
uv run manage.py runserver
```

## Building front-end

To build JavaScript and CSS files, first install npm packages:

```bash
npm install
```

Then build (and watch for changes locally):

```bash
npm run dev
```

## Running Celery

Celery can be used to run background tasks.

Celery requires [Redis](https://redis.io/) as a message broker, so make sure
it is installed and running.

You can run it using:

```bash
celery -A spicy worker -l INFO --pool=solo
```

Or with celery beat (for scheduled tasks):

```bash
celery -A spicy worker -l INFO -B --pool=solo
```

Note: Using the `solo` pool is recommended for development but not for production.

## Installing Git commit hooks

To install the Git commit hooks run the following:

```shell
uv run pre-commit install --install-hooks
```

Once these are installed they will be run on every commit.

For more information see the [docs](https://docs.saaspegasus.com/code-structure#code-formatting).

## Running Tests

To run tests:

**Using make:**

```bash
make test
```

**Native:**

```bash
uv run manage.py test
```

Or to test a specific app/module:

**Using make:**

```bash
make test ARGS='apps.web.tests.test_basic_views --keepdb'
```

**Native:**

```bash
uv run manage.py test apps.web.tests.test_basic_views --keepdb
```

On Linux-based systems you can watch for changes using the following:

```bash
find . -name '*.py' | entr uv run manage.py test apps.web.tests.test_basic_views
```

---

*Built with [SaaS Pegasus](https://www.saaspegasus.com/), the Django SaaS boilerplate.*
