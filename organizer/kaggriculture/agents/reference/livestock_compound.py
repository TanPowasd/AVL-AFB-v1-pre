import math
from collections import Counter, deque


CROPS = {
    "WHEAT": (10, 2, 4, 0, 6), "CARROT": (20, 2, 3, 0, 4),
    "TOMATO": (50, 8, 8, 1, 4), "STRAWBERRY": (100, 10, 10, 2, 4),
    "MELON": (80, 10, 12, 0, 6),
}
ANIMALS = {
    "GOOSE": (300, 4, 1, 4, "EGG", "COOP"),
    "COW": (400, 8, 2, 6, "MILK", "PASTURE"),
    "SHEEP": (500, 6, 3, 6, "WOOL", "PASTURE"),
}
PRODUCTS = tuple(CROPS) + ("EGG", "MILK", "WOOL", "FERTILIZER")
SHOPS = {
    "BAKERY": ("EGG", "WHEAT"), "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"), "YARN_STORE": ("WOOL",),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"), "PET_CAFE": ("CARROT",),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
}
PARAMETERS = {
    "WHEAT": (25, 400, "sqrt", 0.8, "log", 0.2),
    "CARROT": (35, 450, "hinge", 1.0, "sqrt", 0.7),
    "TOMATO": (60, 200, "hinge", 0.4, "sqrt", 0.6),
    "STRAWBERRY": (120, 100, "sqrt", 0.7, "linear", 1.6),
    "MELON": (250, 300, "log", 0.2, "sq", 3.6),
    "EGG": (50, 332, "hinge", 0.4, "log", 0.2),
    "MILK": (160, 122, "sqrt", 0.6, "linear", 1.6),
    "WOOL": (200, 105, "log", 0.2, "sq", 3.2),
    "FERTILIZER": (100, 200, "linear", 0.4, "linear", 0.4),
}


def distance(first, second):
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def travel(first, second):
    horizontal = "EAST" if second[0] > first[0] else "WEST"
    vertical = "SOUTH" if second[1] > first[1] else "NORTH"
    return ([[horizontal]] * abs(first[0] - second[0])
            + [[vertical]] * abs(first[1] - second[1]))


def hire_total(count):
    first, second, total = 1, 1, 0
    for _ in range(count):
        total += first
        first, second = second, first + second
    return total


def inputs_for(operations):
    amounts = Counter()
    for operation in operations:
        if operation[0] == "FEED":
            amounts["WHEAT"] += 1
        elif operation[0] == "FERTILIZE":
            amounts["FERTILIZER"] += 1
        elif operation[0] == "PLACE" and operation[1] in ANIMALS:
            amounts[operation[1]] += 1
    return amounts


def shape(name, amount, scale):
    amount = max(0.0, amount)
    if name == "sqrt":
        return math.sqrt(amount)
    if name == "log":
        return math.log1p(amount)
    if name == "log10":
        return math.log10(1.0 + amount)
    if name == "sq":
        return amount * amount
    if name == "hinge":
        ratio = amount / max(1.0, scale)
        return ratio + 8.0 * max(0.0, ratio - 1.0) ** 2
    return amount


class Economy:
    def __init__(self, observation, day, ours=None, rival=None):
        self.day = day
        self.stock = dict(observation["market"]["inventory"])
        self.params = {}
        patches = observation["market"].get("params") or {}
        for item, values in PARAMETERS.items():
            base, scale, below, up, above, down = values
            parameters = dict(base=base, I0=10000, T=scale,
                              below_func=below, below_target=up,
                              above_func=above, above_target=down)
            parameters.update(patches.get(item) or {})
            self.params[item] = parameters
        shops = observation.get("town", {}).get("unlocked_shops") or []
        self.demand = Counter()
        for shop in shops:
            products = SHOPS.get(shop, ())
            for item in products:
                self.demand[item] += 12 if len(products) == 1 else 6
        self.prior = Counter()
        for products in SHOPS.values():
            for item in products:
                self.prior[item] += (12 if len(products) == 1 else 6) / 8.0
        self.opened = len(shops)
        self.ours = [Counter() for _ in range(30)] if ours is None else ours
        self.rival = [Counter() for _ in range(30)] if rival is None else rival
        self.prices = self.forecast()

    def quote(self, item, stock):
        parameters = self.params[item]
        base, center, scale = parameters["base"], parameters["I0"], parameters["T"]
        below = stock < center
        curve = parameters["below_func"] if below else parameters["above_func"]
        target = parameters["below_target"] if below else parameters["above_target"]
        shift = target * base * shape(curve, abs(stock - center), scale)
        shift /= max(1e-12, shape(curve, scale, scale))
        return max(1, int(round(base + shift if below else base - shift)))

    def forecast(self, extra=None):
        prices = [dict() for _ in range(30)]
        inventory = dict(self.stock)
        for current in range(self.day, 30):
            future_shops = min(8 - self.opened, max(0, current // 3 - self.day // 3))
            for item in PRODUCTS:
                demand = self.demand[item] + self.prior[item] * future_shops
                if item != "FERTILIZER":
                    demand += 1
                inventory[item] -= demand * (0.45 if current == self.day else 1.0)
                units = self.ours[current][item] + self.rival[current][item]
                if extra is not None:
                    units += extra[current][item]
                before = self.quote(item, inventory[item])
                after = self.quote(item, inventory[item] + units)
                prices[current][item] = 0.5 * (before + after)
                if units > 0 and after == 1:
                    lower, upper = inventory[item], inventory[item] + units
                    for _ in range(12):
                        middle = (lower + upper) * 0.5
                        if self.quote(item, middle) > 1:
                            lower = middle
                        else:
                            upper = middle
                    inventory[item] = upper
                else:
                    inventory[item] += units
        return prices

    def add(self, project):
        for current, row in enumerate(project["flows"]):
            self.ours[current].update(row)
        self.prices = self.forecast()



def project_record(rows, cost, label, planted, labor, visit):
    flows = [Counter() for _ in range(30)]
    work_cost = 0.0
    for current, operations, outputs, resources in rows:
        delivery = min(29, current + 1)
        flows[delivery].update(dict(outputs))
        flows[current].subtract(dict(resources))
        if operations:
            work_cost += labor * len(operations) + visit
    return {"rows": rows, "flows": flows, "cost": cost,
            "label": label, "planted": planted, "work": work_cost}


def value_of(project, prices):
    total = -project["cost"] - project["work"]
    for current, operations, outputs, resources in project["rows"]:
        delivery = min(29, current + 1)
        total += sum(prices[delivery].get(item, 1) * count for item, count in outputs)
        total -= sum(prices[current].get(item, 1) * count
                     for item, count in resources if item in PRODUCTS)
    return total


def crop_options(crop, day, tile, labor, visit):
    seed_cost, first, last, interval, maximum = CROPS[crop]
    fresh = tile is None
    planted = day if fresh else int(tile["planted_day"])
    terminal = planted + (first + 3 * interval if interval else last)
    end = max(day, min(29, terminal))
    if planted + first > 29:
        return []
    fertilization_modes = (0, 1, 2, 3) if interval else (0, 1)
    harvest_days = (end,) if interval else tuple(
        current for current in range(max(day, planted + first), min(29, planted + last) + 1))
    if not fresh and not harvest_days and tile.get("yield_units", 0) > 0:
        harvest_days = (day,)
    variants = []
    for fertilization in fertilization_modes:
        for harvest_day in harvest_days:
            for batching in ((0, 1) if interval else (0,)):
                held = (0 if interval else 1) if fresh else int(tile.get("yield_units", 0))
                dry = 1 if fresh else int(tile.get("consecutive_unwatered", 0))
                until = -1 if fresh else int(tile.get("fertilized_until_day", -1))
                expiry = -1 if fresh else int(tile.get("max_lifespan_step", -1))
                rows = []
                for current in range(day, harvest_day + 1):
                    if expiry >= 0 and current * 24 >= expiry and current > day:
                        break
                    age = current - planted
                    already_watered = bool(not fresh and current == day and tile.get("watered_today"))
                    operations = [("PLANT", crop)] if fresh and current == day else []
                    resources, outputs = [], []
                    want_fert = False
                    if fertilization and interval:
                        next_age = age + 1 - first
                        growing = next_age >= 0 and next_age % interval == 0 and next_age // interval < 4
                        if growing and until < current:
                            want_fert = fertilization == 1 or (
                                fertilization == 2 and next_age // interval < 2) or (
                                fertilization == 3 and next_age // interval >= 2)
                    elif fertilization and not interval:
                        want_fert = age == (last + 1) // 2 and until < current
                    window = (last + 1) // 2 <= age <= last if not interval else False
                    useful_water = (window and held < maximum) or want_fert
                    if interval and until >= current:
                        next_age = age + 1 - first
                        useful_water = next_age >= 0 and next_age % interval == 0 and next_age // interval < 4
                    water = not already_watered and (dry >= 1 or useful_water)
                    if current == harvest_day and held > 0 and not window and not useful_water:
                        water = False
                    if want_fert:
                        operations.append(("FERTILIZE",))
                        resources.append(("FERTILIZER", 1))
                        until = current + 2
                        water = not already_watered
                    watered = water or already_watered
                    if water:
                        operations.append(("WATER",))
                        if not interval and window:
                            held = min(maximum, held + (2 if until >= current else 1))
                    ripe = age >= first and held > 0
                    collect = ripe and (current == harvest_day or (
                        interval and (held >= maximum or batching and held >= 2)))
                    if collect:
                        operations.append(("HARVEST",))
                        outputs.append((crop, held))
                        held = 0
                    if operations:
                        rows.append((current, tuple(operations), tuple(outputs), tuple(resources)))
                    if collect and not interval:
                        break
                    dry = 0 if watered else dry + 1
                    if dry >= 2:
                        break
                    if interval:
                        next_age = age + 1 - first
                        if next_age >= 0 and next_age % interval == 0 and next_age // interval < 4:
                            held = min(maximum, held + (2 if watered and until >= current else 1))
                            if next_age // interval == 3:
                                expiry = (current + 2) * 24
                if any(row[2] for row in rows):
                    variants.append(project_record(rows, seed_cost if fresh else 0, crop,
                                                   planted, labor, visit))
    return variants


def animal_options(animal, day, tile, prices, labor, visit):
    cost, first, interval, maximum, product, building = ANIMALS[animal]
    fresh = tile is None
    placed = day if fresh else int(tile.get("placed_day", day))
    variants = []
    if not fresh:
        variants.append(project_record([], 0, animal, placed, labor, visit))
        rows = []
        hungry = int(tile.get("consecutive_unfed", 0))
        held = int(tile.get("yield_units", 0))
        bonus = int(tile.get("pending_care_bonus", 0))
        available = bool(tile.get("fertilizer_available"))
        for current in range(day, 30):
            operations, outputs = [], []
            delivery = min(29, current + 1)
            if held * prices[delivery][product] > labor + visit:
                operations.append(("HARVEST",))
                outputs.append((product, held))
                held = 0
            if available and prices[delivery]["FERTILIZER"] > labor + (0 if operations else visit):
                operations.append(("COLLECT_FERTILIZER",))
                outputs.append(("FERTILIZER", 1))
            if operations:
                rows.append((current, tuple(operations), tuple(outputs), ()))
            fed = current == day and bool(tile.get("fed_today"))
            hungry = 0 if fed else hungry + 1
            if hungry >= 2:
                break
            age = current + 1 - placed - first
            if age >= 0 and age % interval == 0:
                held = min(maximum, held + 1 + (bonus if fed else 0))
                bonus = 0
            if fed and tile.get("cared_today"):
                bonus += 1
            available = True
        variants.append(project_record(rows, 0, animal, placed, labor, visit))
    for care_mode in (0, 1, 2):
        for batching in (0, 1):
            hungry = 0 if fresh else int(tile.get("consecutive_unfed", 0))
            bonus = 0 if fresh else int(tile.get("pending_care_bonus", 0))
            held = 0 if fresh else int(tile.get("yield_units", 0))
            available = False if fresh else bool(tile.get("fertilizer_available"))
            rows = []
            for current in range(day, 30):
                operations, outputs, resources = [], [], []
                if fresh and current == day:
                    operations.extend((("BUILD_" + building,), ("PLACE", animal)))
                    resources.append((animal, 1))
                fed = bool(not fresh and current == day and tile.get("fed_today"))
                cared = bool(not fresh and current == day and tile.get("cared_today"))
                age = current + 1 - placed - first
                production = age >= 0 and age % interval == 0
                next_price = prices[min(29, current + interval)][product]
                wheat_price = prices[current]["WHEAT"]
                care_profitable = next_price > wheat_price + 2.5 * labor
                want_care = care_mode == 1 or care_mode == 2 and care_profitable
                if current < placed + first - 1 and bonus >= maximum - 1:
                    want_care = False
                if current >= 28:
                    want_care = False
                feed = current < 29 and (hungry >= 1 or want_care or production and bonus > 0)
                if fresh and current == day and not want_care:
                    feed = False
                if feed and not fed:
                    operations.append(("FEED",))
                    resources.append(("WHEAT", 1))
                    fed = True
                if want_care and fed and not cared and current < 28:
                    operations.append(("CARE",))
                    cared = True
                should_harvest = held > 0 and (current == 29 or held >= maximum or (
                    batching == 0 and production) or production and held + 1 + bonus > maximum)
                if held > 0 and current == day and prices[min(29, current + 1)][product] >= 0.97 * next_price:
                    should_harvest = True
                if should_harvest:
                    operations.append(("HARVEST",))
                    outputs.append((product, held))
                    held = 0
                if available and prices[min(29, current + 1)]["FERTILIZER"] > labor + 1:
                    operations.append(("COLLECT_FERTILIZER",))
                    outputs.append(("FERTILIZER", 1))
                if operations:
                    rows.append((current, tuple(operations), tuple(outputs), tuple(resources)))
                hungry = 0 if fed else hungry + 1
                if hungry >= 2:
                    break
                if production:
                    held = min(maximum, held + 1 + (bonus if fed else 0))
                    bonus = 0
                if fed and cared:
                    bonus += 1
                available = True
            if any(row[2] for row in rows):
                variants.append(project_record(rows, cost if fresh else 0, animal,
                                               placed, labor, visit))
    return variants



def today_job(project, position, day, tile, prices):
    operations, outputs, resources = (), (), ()
    for current, commands, goods, materials in project["rows"]:
        if current == day:
            operations, outputs, resources = commands, goods, materials
            break
    if not operations:
        return None
    operations = list(operations)
    if tile is not None and tile.get("kind") in ("WEED", "COOP", "PASTURE") and not tile.get("animal"):
        operations.insert(0, ("DIG",))
    present_value = max(0, value_of(project, prices))
    immediate = sum(prices[min(29, day + 1)][item] * count for item, count in outputs)
    risk = False
    if tile and tile.get("kind") == "PLANT":
        risk = tile.get("consecutive_unwatered", 0) >= 1 and ("WATER",) in operations
    if tile and tile.get("animal"):
        risk = tile.get("consecutive_unfed", 0) >= 1 and ("FEED",) in operations
    setup = any(operation[0] in ("PLANT", "PLACE") for operation in operations)
    weight = immediate + (0.62 * present_value if risk else 0.06 * present_value)
    if setup:
        weight += 0.13 * present_value
    weight += sum(8 for operation in operations if operation[0] in ("WATER", "CARE"))
    supplies = inputs_for(operations)
    mask = sum(1 << index for index, item in enumerate(("WHEAT", "FERTILIZER") + tuple(ANIMALS))
               if supplies[item] > 0)
    return {"pos": position, "ops": tuple(operations), "inputs": supplies, "mask": mask,
            "weight": max(4, weight), "risk": risk, "project": project, "outputs": outputs}


def route_length(route, jobs, start, barns, final):
    resources = Counter()
    length, position, output_count = 0, start, 0
    for index in route:
        job = jobs[index]
        resources.update(job["inputs"])
        length += distance(position, job["pos"]) + len(job["ops"])
        output_count += sum(count for _, count in job["outputs"])
        position = job["pos"]
    length += sum(count > 0 for count in resources.values())
    if final and output_count:
        length += min(distance(position, barn) for barn in barns) + 1
    return length


def allocate_routes(jobs, starts, barns, capacity, final):
    best = None
    for mode in (0, 1):
        routes = [[] for _ in starts]
        lengths = [0] * len(starts)
        masks = [0] * len(starts)
        yields = [0] * len(starts)
        ordering = sorted(range(len(jobs)), key=lambda index: (
            jobs[index]["risk"],
            jobs[index]["weight"] / max(1, len(jobs[index]["ops"]) + (1 if mode == 0 else 0.55) * min(
                distance(jobs[index]["pos"], barn) for barn in barns)),
            min(distance(jobs[index]["pos"], barn) for barn in barns)), reverse=True)
        covered = set()
        for index in ordering:
            chosen = None
            job = jobs[index]
            output = sum(count for _, count in job["outputs"])
            for worker, route in enumerate(routes):
                old_length = lengths[worker]
                pickup = (masks[worker] | job["mask"]).bit_count() - masks[worker].bit_count()
                old_return = 0
                if final and yields[worker]:
                    old_return = min(distance(jobs[route[-1]]["pos"], barn) for barn in barns) + 1
                for place in range(len(route) + 1):
                    previous = jobs[route[place - 1]]["pos"] if place else starts[worker]
                    increment = distance(previous, job["pos"]) + len(job["ops"]) + pickup
                    if place < len(route):
                        following = jobs[route[place]]["pos"]
                        increment += distance(job["pos"], following) - distance(previous, following)
                    if final and yields[worker] + output:
                        last = job["pos"] if place == len(route) else jobs[route[-1]]["pos"]
                        increment += min(distance(last, barn) for barn in barns) + 1 - old_return
                    length = old_length + increment
                    if length > capacity:
                        continue
                    criterion = length - old_length + 0.035 * length + 0.001 * worker
                    if chosen is None or criterion < chosen[0]:
                        chosen = criterion, worker, place, length
            if chosen is not None:
                worker = chosen[1]
                routes[worker].insert(chosen[2], index)
                lengths[worker] = chosen[3]
                masks[worker] |= job["mask"]
                yields[worker] += output
                covered.add(index)
        score = sum(jobs[index]["weight"] for index in covered)
        candidate = score, -sum(lengths), routes, covered
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2], best[3], best[0]


class FarmPlanner:
    def __init__(self):
        self.day = -1
        self.player = -1
        self.queues = []
        self.orders = deque()
        self.start = 2
        self.barns = ((4, 4), (5, 4), (4, 5), (5, 5))
        self.economy = None
        self.history = []
        self.option_cache = {}

    def spawn_positions(self, farm, count):
        starts = [tuple(farm["farmer"])]
        starts.extend(tuple(position) for position in farm.get("hands", []))
        occupancy = Counter(starts)
        while len(starts) < count:
            position = min(self.barns, key=lambda barn: (occupancy[barn], self.barns.index(barn)))
            starts.append(position)
            occupancy[position] += 1
        return starts

    def options(self, label, day, tile, position, prices, labor):
        remoteness = min(distance(position, barn) for barn in self.barns)
        visit = labor * (1.15 + 0.30 * remoteness)
        state = tuple(sorted(tile.items())) if tile else ()
        signature = ()
        if label in ANIMALS:
            interval, product = ANIMALS[label][2], ANIMALS[label][4]
            signature = tuple((prices[min(29, current + interval)][product] > prices[current]["WHEAT"] + 2.5 * labor,
                               prices[min(29, current + 1)]["FERTILIZER"] > labor + 1,
                               prices[min(29, current + 1)][product],
                               prices[min(29, current + 1)]["FERTILIZER"])
                              for current in range(day, 30))
            signature += (prices[min(29, day + 1)][product] >= 0.97 * prices[min(29, day + interval)][product],)
        key = label, day, state, signature
        if key not in self.option_cache:
            if label in CROPS:
                variants = crop_options(label, day, tile, labor, 0)
            else:
                variants = animal_options(label, day, tile, prices, labor, 0)
            self.option_cache[key] = variants
        return [dict(project, work=project["work"] + visit * len(project["rows"]))
                for project in self.option_cache[key]]

    def public_flows(self, farm, day, prices, labor):
        flows = [Counter() for _ in range(30)]
        for vertical, row in enumerate(farm["tiles"]):
            for horizontal, tile in enumerate(row):
                if not isinstance(tile, dict):
                    continue
                label = tile.get("animal") or tile.get("crop")
                if label not in CROPS and label not in ANIMALS:
                    continue
                options = self.options(label, day, tile, (horizontal, vertical), prices, labor)
                if not options:
                    continue
                project = max(options, key=lambda option: value_of(option, prices))
                for current, changes in enumerate(project["flows"]):
                    flows[current].update(changes)
        return flows


    def purchase_orders(self, observation, jobs, covered, count, land_order):
        farm = observation["farms"][observation["player"]]
        private = observation["private"]
        shed = private.get("shed") or {}
        resources, seeds = Counter(), Counter()
        for index in covered:
            resources.update(jobs[index]["inputs"])
            for operation in jobs[index]["ops"]:
                if operation[0] == "PLANT":
                    seeds[operation[1]] += 1
        orders, expense, revenue = [], 0.0, 0.0
        inventory = dict(observation["market"]["inventory"])
        for item in PRODUCTS:
            quantity = max(0, shed.get(item, 0) - resources[item])
            if quantity:
                orders.append(["SELL", item, quantity])
                for _ in range(quantity):
                    price = self.economy.quote(item, inventory[item])
                    revenue += price
                    if price > 1:
                        inventory[item] += 1
        hired = max(0, count - 1 - len(farm.get("hands", [])))
        orders.extend([["HIRE"] for _ in range(hired)])
        expense += hire_total(farm.get("hires_today", 0) + hired) - hire_total(farm.get("hires_today", 0))
        if land_order:
            orders.append(["BUY_LAND"])
            expense += (1000, 2000, 4000)[len(farm["unlocked_quadrants"]) - 1]
        for animal in ANIMALS:
            quantity = max(0, resources[animal] - shed.get(animal, 0))
            if quantity:
                orders.append(["BUY_ANIMAL", animal, quantity])
                expense += ANIMALS[animal][0] * quantity
        for item in ("WHEAT", "FERTILIZER"):
            quantity = max(0, resources[item] - shed.get(item, 0))
            if quantity:
                orders.append(["BUY_PRODUCT", item, quantity])
                for _ in range(quantity):
                    inventory[item] -= 1
                    expense += self.economy.quote(item, inventory[item])
        for crop in CROPS:
            quantity = max(0, seeds[crop] - private.get("seeds", {}).get(crop, 0))
            if quantity:
                orders.append(["BUY_SEED", crop, quantity])
                expense += CROPS[crop][0] * quantity
        return orders, expense, revenue

    def plan(self, observation):
        self.option_cache = {}
        day, player = int(observation["day"]), int(observation["player"])
        farm = observation["farms"][player]
        private = observation["private"]
        tiles = farm["tiles"]
        board_size = len(tiles)
        half = board_size // 2
        self.barns = ((half - 1, half - 1), (half, half - 1),
                      (half - 1, half), (half, half))
        labor = 3.7 + 0.035 * sum(isinstance(tile, dict) and tile.get("kind") != "WEED"
                                 for row in tiles for tile in row)
        initial = Economy(observation, day)
        rival = self.public_flows(observation["farms"][1 - player], day, initial.prices, labor)
        ours = self.public_flows(farm, day, initial.prices, labor)
        economy = Economy(observation, day, ours, rival)
        economy.ours = [Counter() for _ in range(30)]
        jobs, available = [], []
        for vertical, row in enumerate(tiles):
            for horizontal, tile in enumerate(row):
                position = horizontal, vertical
                if tile == "LOCKED":
                    continue
                if isinstance(tile, dict) and (tile.get("crop") or tile.get("animal")):
                    label = tile.get("animal") or tile.get("crop")
                    variants = self.options(label, day, tile, position, economy.prices, labor)
                    if variants:
                        project = max(variants, key=lambda option: value_of(option, economy.prices))
                        economy.add(project)
                        job = today_job(project, position, day, tile, economy.prices)
                        if job:
                            jobs.append(job)
                    continue
                available.append((position, tile))
        shed = private.get("shed") or {}
        sale_cash = sum(economy.quote(item, economy.stock[item]) * count
                        for item, count in shed.items() if item in PRODUCTS)
        wealth = farm["money"] + sale_cash
        new_cost = 0
        land_order = False
        quadrants = farm.get("unlocked_quadrants") or ["NW"]
        land_cost = (1000, 2000, 4000)[len(quadrants) - 1] if len(quadrants) < 4 else 0
        if len(available) < 5 and day <= 18 and land_cost and wealth > land_cost + 650:
            quadrant = ("NE", "SW", "SE")[len(quadrants) - 1]
            for vertical, row in enumerate(tiles):
                for horizontal, tile in enumerate(row):
                    position_quadrant = ("N" if vertical < half else "S") + ("W" if horizontal < half else "E")
                    if tile == "LOCKED" and position_quadrant == quadrant:
                        available.append(((horizontal, vertical), None))
            land_order = True
            new_cost += land_cost
        available.sort(key=lambda entry: (min(distance(entry[0], barn) for barn in self.barns),
                                           bool(entry[1]), entry[0][1], entry[0][0]))
        labels = tuple(CROPS) + tuple(ANIMALS)
        for position, tile in available:
            if day == 29 or len(jobs) >= 100:
                break
            options = []
            for label in labels:
                price = CROPS[label][0] if label in CROPS else ANIMALS[label][0]
                if wealth - new_cost < price + max(180, hire_total(max(5, (len(jobs) + 4) // 5))):
                    continue
                if label in ANIMALS and day > 20:
                    continue
                if label in ANIMALS and day < 8 and sum(job["project"]["label"] in ANIMALS for job in jobs) >= 10:
                    continue
                variants = self.options(label, day, None, position, economy.prices, labor)
                if not variants:
                    continue
                project = max(variants, key=lambda option: value_of(option, economy.prices))
                score = value_of(project, economy.prices)
                if score <= 30:
                    continue
                after = economy.forecast(project["flows"])
                score = value_of(project, after)
                for current in range(day, 30):
                    for item in PRODUCTS:
                        swing = after[current][item] - economy.prices[current][item]
                        score += swing * (max(0, economy.ours[current][item])
                                          - 0.20 * max(0, economy.rival[current][item]))
                if day <= 6:
                    score /= 1 + 0.00025 * price
                if score > 25:
                    options.append((score, project, price))
            if not options:
                continue
            _, project, price = max(options, key=lambda entry: entry[0])
            job = today_job(project, position, day, tile, economy.prices)
            if job is None:
                continue
            jobs.append(job)
            new_cost += price
            economy.add(project)
        self.economy = economy
        final = day == 29
        route_limit = 23 if final else 24
        best = None
        for count in range(1, 22):
            wages = hire_total(count - 1)
            if wages > wealth - 30 and count > 2:
                continue
            starts = self.spawn_positions(farm, count)
            start = 2
            for _ in range(3):
                routes, covered, score = allocate_routes(jobs, starts, self.barns, route_limit - start, final)
                orders, expense, revenue = self.purchase_orders(observation, jobs, covered, count, land_order)
                actual_start = max(start, (len(orders) + 9) // 10)
                if actual_start == start:
                    break
                start = actual_start
            if expense > farm["money"] + 0.98 * revenue - 5:
                continue
            score -= wages
            if best is None or score > best[0]:
                best = score, count, starts, routes, covered, start, orders
            if len(covered) == len(jobs):
                break
        if best is None:
            reduced = []
            for job in jobs:
                if any(operation[0] in ("PLANT", "PLACE") for operation in job["ops"]):
                    continue
                horizontal, vertical = job["pos"]
                tile = tiles[vertical][horizontal]
                skip_feed = isinstance(tile, dict) and tile.get("animal") and tile.get("consecutive_unfed", 0) == 0
                operations = tuple(operation for operation in job["ops"] if operation[0] != "FERTILIZE"
                                   and not (skip_feed and operation[0] in ("FEED", "CARE")))
                if not operations:
                    continue
                supplies = inputs_for(operations)
                mask = sum(1 << offset for offset, item in enumerate(("WHEAT", "FERTILIZER") + tuple(ANIMALS))
                           if supplies[item] > 0)
                reduced.append(dict(job, ops=operations, inputs=supplies, mask=mask))
            jobs = reduced
            land_order = False
            for count in range(1, 15):
                starts = self.spawn_positions(farm, count)
                routes, covered, score = allocate_routes(jobs, starts, self.barns, route_limit - 2, final)
                orders, expense, revenue = self.purchase_orders(observation, jobs, covered, count, False)
                if len(orders) > 20 or expense > farm["money"] + 0.98 * revenue:
                    continue
                score -= hire_total(count - 1)
                if best is None or score > best[0]:
                    best = score, count, starts, routes, covered, 2, orders
                if len(covered) == len(jobs):
                    break
        if best is None:
            starts = self.spawn_positions(farm, 1)
            routes, covered, score = allocate_routes(jobs, starts, self.barns, route_limit - 2, final)
            orders, expense, revenue = self.purchase_orders(observation, jobs, covered, 1, land_order)
            best = score, 1, starts, routes, covered, 2, orders
        _, count, starts, routes, covered, self.start, orders = best
        self.orders = deque(orders)
        self.queues = []
        for worker, route in enumerate(routes):
            queue = []
            supplies = Counter()
            for index in route:
                supplies.update(jobs[index]["inputs"])
            for item, quantity in supplies.items():
                if quantity > 0:
                    queue.append(["PICKUP", item, quantity])
            position = starts[worker]
            outputs = 0
            for index in route:
                job = jobs[index]
                queue.extend(travel(position, job["pos"]))
                queue.extend(list(operation) for operation in job["ops"])
                position = job["pos"]
                outputs += sum(quantity for _, quantity in job["outputs"])
            if outputs:
                barn = min(self.barns, key=lambda target: distance(position, target))
                delivery = travel(position, barn) + [["DROP"]]
                if self.start + len(queue) + len(delivery) <= route_limit:
                    queue.extend(delivery)
            self.queues.append(deque(queue))
        self.day, self.player = day, player
        self.history.append((day, count, len(jobs), len(covered), new_cost))


    def act(self, observation):
        player = int(observation["player"])
        farm = observation["farms"][player]
        day, hour = int(observation["day"]), int(observation["hour"])
        if day != self.day or player != self.player or observation.get("step", 0) == 0:
            self.start = 2
            self.plan(observation)
        positions = [farm["farmer"]] + list(farm.get("hands") or [])
        inventories = observation["private"].get("inventories") or []
        actions = []
        for index, position in enumerate(positions):
            if hour < self.start:
                actions.append(["PASS"])
            elif index < len(self.queues) and self.queues[index]:
                actions.append(self.queues[index].popleft())
            elif index < len(inventories) and any(inventories[index].values()):
                barn = min(self.barns, key=lambda target: distance(position, target))
                actions.append(travel(position, barn)[0] if tuple(position) != barn else ["DROP"])
            else:
                actions.append(["PASS"])
        orders = []
        while self.orders and len(orders) < 10:
            orders.append(self.orders.popleft())
        if not self.orders and len(orders) < 10 and hour >= self.start:
            reserve = Counter()
            for queue in self.queues:
                for operation in queue:
                    if operation[0] == "PICKUP":
                        reserve[operation[1]] += operation[2]
            available = Counter(observation["private"].get("shed") or {})
            for index, action in enumerate(actions):
                if action[0] == "DROP" and index < len(inventories):
                    available.update(inventories[index])
                elif action[0] == "PICKUP":
                    available[action[1]] -= action[2]
            for item in PRODUCTS:
                quantity = available[item] - reserve[item]
                if quantity > 0 and len(orders) < 10:
                    orders.append(["SELL", item, quantity])
        return {"farmer": actions[0], "hands": actions[1:], "market": orders}


_planners = {}


def act(observation):
    player = int(observation["player"])
    if player not in _planners or observation.get("step", 0) == 0:
        _planners[player] = FarmPlanner()
    return _planners[player].act(observation)
