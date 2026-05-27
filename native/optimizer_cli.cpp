#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <numeric>
#include <queue>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <ctime>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <nlohmann/json.hpp>

using json = nlohmann::ordered_json;
namespace fs = std::filesystem;

namespace {

constexpr int TYPE_LEGENDARY = 3;
constexpr int TYPE_GLYPH_SOCKET = 4;
constexpr double GLYPH_ROUTE_BONUS_FACTOR = 0.5;
constexpr int MAX_GLYPH_CANDIDATES_PER_SOCKET = 8;
constexpr double UNMET_PREFERRED_GLYPH_WEIGHT_FACTOR = 0.25;
constexpr int DEFAULT_MAX_ROUTES = 3000;
constexpr int DEFAULT_CANDIDATE_TARGETS = 320;
constexpr double RARE_REQUIREMENT_SCALE_PER_BOARD = 0.65;
constexpr double RARE_BONUS_ROUTE_HINT_FACTOR = 0.6;
constexpr int LOCAL_IMPROVEMENT_MAX_PASSES = 8;
constexpr int LOCAL_IMPROVEMENT_REMOVE_CANDIDATES = 36;
constexpr int LOCAL_IMPROVEMENT_ADD_CANDIDATES = 48;

struct Gate {
    std::string id;
    std::string side;
};

struct Node {
    std::string id;
    std::string board_id;
    int x = 0;
    int y = 0;
    std::string type;
    int cost = 1;
    std::map<std::string, double> stats;
    std::map<std::string, double> requirements;
    std::map<std::string, double> bonus_stats;
    json name = json::object();
};

struct Board {
    std::string id;
    std::string class_slug;
    json name = json::object();
    int width = 0;
    int height = 0;
    std::string start_node;
    std::vector<Node> nodes;
    std::unordered_map<std::string, size_t> node_by_id;
    std::vector<std::pair<std::string, std::string>> edges;
    std::vector<Gate> gates;
    std::vector<std::string> glyph_sockets;
    std::vector<std::string> legendary_nodes;
};

struct Glyph {
    std::string id;
    std::string class_slug;
    json name = json::object();
    std::map<std::string, double> radius;
    std::vector<std::string> threshold_stats;
    std::string bonus_text_en;
    std::string bonus_text_ru;
    std::vector<std::string> skill_tag_names;
};

struct ClassRef {
    std::string class_slug;
    json name = json::object();
    std::vector<std::string> boards;
    std::vector<std::string> glyphs;
    std::vector<std::string> available_stats;
};

struct GlyphRouteTuning {
    double activation = 1.0;
    double scaling = 1.0;
    double future = 0.35;
    double synergy = 0.25;
    double scarcity = 0.30;
    double fill_target = 1.20;
    double max_bonus_multiplier = 1.60;
};

struct WeightModel {
    std::map<std::string, double> weights;
    std::map<std::string, double> minimums;
    std::map<std::string, double> scheme_weights;
    std::vector<std::string> scheme_order;
    bool scheme_is_dict = false;
    std::map<std::string, double> glyph_weights;
    GlyphRouteTuning glyph_route;
};

struct GraphNode {
    std::string id;
    std::string board_id;
    int x = 0;
    int y = 0;
    std::string type;
    int cost = 1;
    std::map<std::string, double> stats;
    std::map<std::string, double> requirements;
    std::map<std::string, double> bonus_stats;
    int requirement_board_depth = 0;
    json name = json::object();
};

struct Attachment {
    std::string from;
    std::string to;
};

struct Graph {
    std::vector<GraphNode> nodes;
    std::unordered_map<std::string, int> node_index;
    std::vector<std::vector<int>> adjacency;
    std::vector<std::string> board_order;
    std::map<std::string, int> rotations;
    std::vector<Attachment> attachments;
    std::vector<int> glyph_sockets;
    std::vector<int> legendary_nodes;
    int start_index = -1;
};

struct RouteInput {
    std::vector<std::string> node_ids;
    std::vector<int> costs;
    std::vector<double> scores;
    std::vector<int> types;
    std::vector<std::vector<int>> adjacency;
    std::vector<int> targets;
    int start_index = -1;
    int points = 0;
};

struct RouteStep {
    int target = -1;
    std::vector<int> path;
    std::vector<int> added_nodes;
    int cost = 0;
    double gain = 0.0;
    int points_used = 0;
};

struct RouteOutput {
    std::vector<unsigned char> selected;
    std::vector<RouteStep> steps;
};

struct Candidate {
    bool valid = false;
    double ratio = -std::numeric_limits<double>::infinity();
    double gain = -std::numeric_limits<double>::infinity();
    int negative_cost = std::numeric_limits<int>::min();
    int target = -1;
    std::vector<int> path;
    std::vector<int> new_nodes;
};

struct GlyphInfo {
    std::string threshold_stat;
    double requirement = 25.0;
    double radius = 0.0;
    std::string bonus_stat;
    double scaling_value_per_5 = 0.0;
};

struct GlyphEvaluation {
    int glyph_index = -1;
    int socket_index = -1;
    double score = 0.0;
    double stat_in_radius = 0.0;
    double requirement = 25.0;
    bool requirement_met = false;
    double radius = 0.0;
    std::string bonus_stat;
    double scaling_value_per_5 = 0.0;
    std::vector<std::string> warnings;
};

struct ScoringContext {
    std::vector<GlyphInfo> glyph_info;
    std::unordered_map<long long, std::vector<int>> radius_nodes;
};

struct ScoredRoute {
    json payload;
    RouteOutput route;
    double score = 0.0;
    int points_used = 0;
    int selected_node_count = 0;
    int local_swaps = 0;
};

struct LocalSwap {
    int removed = -1;
    int added = -1;
    double score_before = 0.0;
    double score_after = 0.0;
};

struct Options {
    std::string command;
    fs::path profile_path;
    std::string class_slug;
    int points = 0;
    bool legendary_glyphs = true;
    fs::path weights_path;
    std::map<std::string, double> starting_stats;
    fs::path data_root;
    int max_routes = DEFAULT_MAX_ROUTES;
    int candidate_targets = DEFAULT_CANDIDATE_TARGETS;
    int workers = 0;
    bool include_route_steps = false;
    bool no_html = false;
    std::vector<std::string> scheme;
};

std::string read_file(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("file not found: " + path.string());
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return buffer.str();
}

json read_json(const fs::path& path) {
    return json::parse(read_file(path));
}

double as_double(const json& value, double fallback = 0.0) {
    if (value.is_number()) {
        return value.get<double>();
    }
    if (value.is_string()) {
        try {
            return std::stod(value.get<std::string>());
        } catch (const std::exception&) {
            return fallback;
        }
    }
    return fallback;
}

double bounded_double(double value, double fallback, double min_value, double max_value) {
    if (!std::isfinite(value)) {
        return fallback;
    }
    return std::clamp(value, min_value, max_value);
}

void read_optional_tuning_double(
    const json& object,
    const std::string& key,
    double& target,
    double min_value,
    double max_value
) {
    auto it = object.find(key);
    if (it == object.end()) {
        return;
    }
    target = bounded_double(as_double(*it, target), target, min_value, max_value);
}

void read_glyph_route_tuning(const json& value, GlyphRouteTuning& tuning) {
    if (!value.is_object()) {
        throw std::runtime_error("weights field 'glyph_route' must be an object");
    }
    read_optional_tuning_double(value, "activation", tuning.activation, 0.0, 10.0);
    read_optional_tuning_double(value, "scaling", tuning.scaling, 0.0, 10.0);
    read_optional_tuning_double(value, "future", tuning.future, 0.0, 10.0);
    read_optional_tuning_double(value, "synergy", tuning.synergy, 0.0, 10.0);
    read_optional_tuning_double(value, "scarcity", tuning.scarcity, 0.0, 10.0);
    read_optional_tuning_double(value, "fill_target", tuning.fill_target, 1.0, 4.0);
    read_optional_tuning_double(value, "max_bonus_multiplier", tuning.max_bonus_multiplier, 0.10, 20.0);
}

std::map<std::string, double> read_float_map(const json& value) {
    std::map<std::string, double> result;
    if (!value.is_object()) {
        return result;
    }
    for (auto it = value.begin(); it != value.end(); ++it) {
        result[it.key()] = as_double(it.value());
    }
    return result;
}

std::vector<std::string> read_string_array(const json& value) {
    std::vector<std::string> result;
    if (!value.is_array()) {
        return result;
    }
    result.reserve(value.size());
    for (const auto& item : value) {
        if (item.is_string()) {
            result.push_back(item.get<std::string>());
        }
    }
    return result;
}



json object_or_empty(const json& value) {
    return value.is_object() ? value : json::object();
}

std::string localized_name(const json& name, const std::string& fallback) {
    if (name.is_object()) {
        auto ru = name.find("ru");
        if (ru != name.end() && ru->is_string() && !ru->get<std::string>().empty()) {
            return ru->get<std::string>();
        }
        auto en = name.find("en");
        if (en != name.end() && en->is_string() && !en->get<std::string>().empty()) {
            return en->get<std::string>();
        }
    }
    return fallback;
}

double weight_for(const std::map<std::string, double>& values, const std::string& key, double fallback = 0.0) {
    auto it = values.find(key);
    return it == values.end() ? fallback : it->second;
}

double rare_requirement_scale_for_board_depth(int board_depth) {
    if (board_depth <= 0) {
        return 1.0;
    }
    return 1.0 + RARE_REQUIREMENT_SCALE_PER_BOARD * board_depth;
}

std::map<std::string, double> scale_requirements(const std::map<std::string, double>& requirements, int board_depth) {
    std::map<std::string, double> result;
    if (requirements.empty()) {
        return result;
    }
    double scale = rare_requirement_scale_for_board_depth(board_depth);
    for (const auto& [stat, required] : requirements) {
        result[stat] = std::round(required * scale);
    }
    return result;
}

std::pair<int, int> rotate_point(int x, int y, int width, int height, int rotation) {
    int normalized = ((rotation % 360) + 360) % 360;
    if (normalized == 0) return {x, y};
    if (normalized == 90) return {height - 1 - y, x};
    if (normalized == 180) return {width - 1 - x, height - 1 - y};
    if (normalized == 270) return {y, width - 1 - x};
    throw std::runtime_error("unsupported rotation: " + std::to_string(rotation));
}

std::string rotate_side(const std::string& side, int rotation) {
    static const std::vector<std::string> sides = {"top", "right", "bottom", "left"};
    auto it = std::find(sides.begin(), sides.end(), side);
    if (it == sides.end()) {
        return side;
    }
    int shift = (((rotation % 360) + 360) % 360) / 90;
    return sides[(static_cast<int>(it - sides.begin()) + shift) % 4];
}

std::string opposite_side(const std::string& side) {
    if (side == "top") return "bottom";
    if (side == "bottom") return "top";
    if (side == "left") return "right";
    if (side == "right") return "left";
    return "";
}

std::pair<int, int> side_delta(const std::string& side) {
    if (side == "top") return {0, -1};
    if (side == "bottom") return {0, 1};
    if (side == "left") return {-1, 0};
    if (side == "right") return {1, 0};
    return {0, 0};
}

int node_type_code(const std::string& node_type) {
    if (node_type == "normal") return 0;
    if (node_type == "magic") return 1;
    if (node_type == "rare") return 2;
    if (node_type == "legendary") return 3;
    if (node_type == "glyph_socket") return 4;
    if (node_type == "board_gate") return 5;
    return 0;
}

ClassRef load_class(const fs::path& data_root, const std::string& class_slug) {
    json raw = read_json(data_root / "classes" / (class_slug + ".json"));
    if (raw.value("class", "") != class_slug) {
        throw std::runtime_error("class reference mismatch: " + class_slug);
    }
    ClassRef ref;
    ref.class_slug = raw.value("class", "");
    ref.name = object_or_empty(raw.value("name", json::object()));
    ref.boards = read_string_array(raw.value("boards", json::array()));
    ref.glyphs = read_string_array(raw.value("glyphs", json::array()));
    ref.available_stats = read_string_array(raw.value("available_stats", json::array()));
    return ref;
}

Board load_board(const fs::path& data_root, const std::string& class_slug, const std::string& board_id) {
    json raw = read_json(data_root / "boards" / class_slug / (board_id + ".json"));
    Board board;
    board.id = raw.value("id", board_id);
    board.class_slug = raw.value("class", class_slug);
    board.name = object_or_empty(raw.value("name", json::object()));
    board.width = raw.value("width", 0);
    board.height = raw.value("height", 0);
    if (raw.contains("start_node") && raw["start_node"].is_string()) {
        board.start_node = raw["start_node"].get<std::string>();
    }

    for (const auto& item : raw.value("nodes", json::array())) {
        Node node;
        node.id = item.value("id", "");
        node.board_id = board.id;
        node.x = item.value("x", 0);
        node.y = item.value("y", 0);
        node.type = item.value("type", "normal");
        node.cost = item.value("cost", 1);
        node.stats = read_float_map(item.value("stats", json::object()));
        node.requirements = read_float_map(item.value("requirements", json::object()));
        node.bonus_stats = read_float_map(item.value("bonus_stats", json::object()));
        node.name = object_or_empty(item.value("name", json::object()));
        board.node_by_id[node.id] = board.nodes.size();
        board.nodes.push_back(std::move(node));
    }
    for (const auto& edge : raw.value("edges", json::array())) {
        if (edge.is_array() && edge.size() >= 2) {
            board.edges.push_back({edge[0].get<std::string>(), edge[1].get<std::string>()});
        }
    }
    for (const auto& item : raw.value("gates", json::array())) {
        Gate gate;
        gate.id = item.value("id", "");
        gate.side = item.value("side", "");
        board.gates.push_back(std::move(gate));
    }
    board.glyph_sockets = read_string_array(raw.value("glyph_sockets", json::array()));
    board.legendary_nodes = read_string_array(raw.value("legendary_nodes", json::array()));
    return board;
}

Glyph load_glyph(const fs::path& data_root, const std::string& class_slug, const std::string& glyph_id) {
    json raw = read_json(data_root / "glyphs" / class_slug / (glyph_id + ".json"));
    Glyph glyph;
    glyph.id = raw.value("id", glyph_id);
    glyph.class_slug = raw.value("class", class_slug);
    glyph.name = object_or_empty(raw.value("name", json::object()));
    glyph.radius = read_float_map(raw.value("radius", json::object()));
    json bonus = raw.value("bonus_text", json::object());
    glyph.bonus_text_en = bonus.value("en", "");
    glyph.bonus_text_ru = bonus.value("ru", "");
    for (const auto& item : raw.value("threshold_attributes", json::array())) {
        if (item.contains("stat_key") && item["stat_key"].is_string()) {
            glyph.threshold_stats.push_back(item["stat_key"].get<std::string>());
        }
    }
    for (const auto& item : raw.value("skill_tags", json::array())) {
        if (item.contains("name") && item["name"].is_string()) {
            glyph.skill_tag_names.push_back(item["name"].get<std::string>());
        }
    }
    return glyph;
}

std::map<std::string, Board> load_boards(const fs::path& data_root, const ClassRef& class_ref) {
    std::map<std::string, Board> boards;
    for (const std::string& board_id : class_ref.boards) {
        boards.emplace(board_id, load_board(data_root, class_ref.class_slug, board_id));
    }
    return boards;
}

std::vector<Glyph> load_glyphs(const fs::path& data_root, const ClassRef& class_ref) {
    std::vector<Glyph> glyphs;
    glyphs.reserve(class_ref.glyphs.size());
    for (const std::string& glyph_id : class_ref.glyphs) {
        glyphs.push_back(load_glyph(data_root, class_ref.class_slug, glyph_id));
    }
    return glyphs;
}

WeightModel load_weights(const fs::path& weights_path) {
    json raw = read_json(weights_path);
    if (!raw.contains("weights") || !raw["weights"].is_object()) {
        throw std::runtime_error("weights file must contain object field 'weights'");
    }
    WeightModel model;
    model.weights = read_float_map(raw["weights"]);
    model.minimums = read_float_map(raw.value("minimums", json::object()));
    model.glyph_weights = read_float_map(raw.value("glyphs", json::object()));
    if (raw.contains("glyph_route")) {
        read_glyph_route_tuning(raw["glyph_route"], model.glyph_route);
    }
    if (raw.contains("scheme")) {
        const json& scheme = raw["scheme"];
        if (scheme.is_array()) {
            model.scheme_order = read_string_array(scheme);
            model.scheme_is_dict = false;
        } else if (scheme.is_object()) {
            model.scheme_is_dict = true;
            for (auto it = scheme.begin(); it != scheme.end(); ++it) {
                model.scheme_order.push_back(it.key());
                model.scheme_weights[it.key()] = as_double(it.value());
            }
        }
    }
    return model;
}

double priority_for_type(const GraphNode& node, const WeightModel& weights) {
    double base = 0.0;
    if (node.type == "normal") base = 0.1;
    else if (node.type == "magic") base = 0.2;
    else if (node.type == "rare") base = 0.3;
    else if (node.type == "legendary") base = 0.4;
    if (weights.scheme_is_dict && node.type == "legendary") {
        auto it = weights.scheme_weights.find(node.board_id);
        if (it != weights.scheme_weights.end()) {
            return it->second;
        }
    }
    return base;
}

double priority_for_type(const Node& node, const WeightModel& weights) {
    GraphNode graph_node;
    graph_node.board_id = node.board_id;
    graph_node.type = node.type;
    return priority_for_type(graph_node, weights);
}

double weighted_stats_score(const std::map<std::string, double>& stats, const WeightModel& weights) {
    double score = 0.0;
    for (const auto& [stat, value] : stats) {
        score += value * weight_for(weights.weights, stat);
    }
    return score;
}

double node_base_score(const GraphNode& node, const WeightModel& weights) {
    double score = priority_for_type(node, weights);
    score += weighted_stats_score(node.stats, weights);
    return score;
}

double node_base_score(const Node& node, const WeightModel& weights) {
    double score = priority_for_type(node, weights);
    score += weighted_stats_score(node.stats, weights);
    return score;
}

double glyph_socket_route_bonus(const WeightModel& weights) {
    double preferred = 0.0;
    for (const auto& [_, value] : weights.glyph_weights) {
        preferred = std::max(preferred, value);
    }
    return std::max({weight_for(weights.weights, "glyph_socket"), weight_for(weights.weights, "glyph_bonus"), preferred});
}

double route_node_score(
    const GraphNode& node,
    const WeightModel& weights,
    const std::unordered_map<int, double>* route_bonuses = nullptr,
    int index = -1
) {
    double score = node_base_score(node, weights);
    // In the heuristic we add a discounted bonus contribution even without checking requirements.
    // This is intentional to guide the search toward promising rares; the real gating check
    // happens later in the full route_score_value.
    if (!node.bonus_stats.empty()) {
        score += weighted_stats_score(node.bonus_stats, weights) * RARE_BONUS_ROUTE_HINT_FACTOR;
    }
    if (route_bonuses && index >= 0) {
        auto it = route_bonuses->find(index);
        if (it != route_bonuses->end()) {
            score += it->second;
        }
    }
    if (node.type == "glyph_socket") {
        score += glyph_socket_route_bonus(weights);
    }
    return score;
}

double board_potential(const Board& board, const WeightModel& weights) {
    double score = 0.0;
    double socket_bonus = glyph_socket_route_bonus(weights);
    for (const Node& node : board.nodes) {
        double value = node_base_score(node, weights);
        if (node.type == "glyph_socket") {
            value += socket_bonus;
        }
        score += std::max(value, 0.0);
    }
    return score;
}

std::vector<std::string> effective_scheme(const WeightModel& weights, const ClassRef& class_ref, const Options& options) {
    std::vector<std::string> raw = !options.scheme.empty() ? options.scheme : weights.scheme_order;
    if (raw.empty()) {
        return {};
    }
    std::unordered_set<std::string> available(class_ref.boards.begin(), class_ref.boards.end());
    std::vector<std::string> result;
    for (const std::string& board_id : raw) {
        if (available.count(board_id)) {
            result.push_back(board_id);
        }
    }
    return result;
}

void permutations_of_length(
    const std::vector<std::string>& items,
    int target_length,
    std::vector<std::string>& current,
    std::vector<unsigned char>& used,
    std::vector<std::vector<std::string>>& output
) {
    if (static_cast<int>(current.size()) == target_length) {
        output.push_back(current);
        return;
    }
    for (size_t i = 0; i < items.size(); ++i) {
        if (used[i]) continue;
        used[i] = 1;
        current.push_back(items[i]);
        permutations_of_length(items, target_length, current, used, output);
        current.pop_back();
        used[i] = 0;
    }
}

std::vector<std::vector<std::string>> board_sequences(
    const ClassRef& class_ref,
    const std::map<std::string, Board>& boards,
    const WeightModel& weights,
    const std::vector<std::string>& scheme
) {
    std::string starter = std::find(class_ref.boards.begin(), class_ref.boards.end(), "starter") != class_ref.boards.end()
        ? "starter"
        : class_ref.boards.front();
    std::vector<std::string> others;
    if (!scheme.empty()) {
        for (const std::string& board_id : scheme) {
            if (board_id != starter) {
                others.push_back(board_id);
            }
        }
    } else {
        for (const std::string& board_id : class_ref.boards) {
            if (board_id != starter) {
                others.push_back(board_id);
            }
        }
        std::sort(others.begin(), others.end(), [&](const std::string& left, const std::string& right) {
            double left_score = board_potential(boards.at(left), weights);
            double right_score = board_potential(boards.at(right), weights);
            if (std::abs(left_score - right_score) > 1e-12) {
                return left_score > right_score;
            }
            return left < right;
        });
    }

    std::vector<std::vector<std::string>> sequences;
    for (int length = static_cast<int>(others.size()); length > 0; --length) {
        std::vector<std::string> current;
        std::vector<unsigned char> used(others.size(), 0);
        std::vector<std::vector<std::string>> perms;
        permutations_of_length(others, length, current, used, perms);
        for (auto& perm : perms) {
            std::vector<std::string> sequence;
            sequence.push_back(starter);
            sequence.insert(sequence.end(), perm.begin(), perm.end());
            sequences.push_back(std::move(sequence));
        }
    }
    sequences.push_back({starter});
    return sequences;
}

struct OpenGate {
    std::string board_id;
    int x = 0;
    int y = 0;
    std::string gate_id;
    std::string side;
};

void generate_layouts_dfs(
    const std::map<std::string, Board>& boards,
    const std::vector<std::string>& sequence,
    int index,
    std::set<std::pair<int, int>>& occupied,
    std::vector<OpenGate>& open_gates,
    std::vector<Attachment>& attachments,
    std::map<std::string, int>& rotations,
    const std::function<void(const std::map<std::string, int>&, const std::vector<Attachment>&)>& callback
) {
    if (index == static_cast<int>(sequence.size())) {
        callback(rotations, attachments);
        return;
    }

    const std::string& next_board_id = sequence[index];
    const Board& next_board = boards.at(next_board_id);
    const std::string& previous_board_id = sequence[index - 1];

    for (size_t i = 0; i < open_gates.size(); ++i) {
        const OpenGate previous_gate = open_gates[i];
        if (previous_gate.board_id != previous_board_id) {
            continue;
        }
        std::string required_side = opposite_side(previous_gate.side);
        if (required_side.empty()) {
            continue;
        }
        auto [dx, dy] = side_delta(previous_gate.side);
        int nx = previous_gate.x + dx;
        int ny = previous_gate.y + dy;
        if (occupied.count({nx, ny})) {
            continue;
        }

        for (const Gate& next_gate : next_board.gates) {
            if (next_gate.side.empty()) {
                continue;
            }
            for (int rotation : {0, 90, 180, 270}) {
                if (rotate_side(next_gate.side, rotation) != required_side) {
                    continue;
                }

                occupied.insert({nx, ny});
                rotations[next_board_id] = rotation;
                attachments.push_back({previous_gate.gate_id, next_gate.id});

                std::vector<OpenGate> next_open_gates;
                next_open_gates.reserve(open_gates.size() + next_board.gates.size());
                for (size_t j = 0; j < open_gates.size(); ++j) {
                    if (j != i) {
                        next_open_gates.push_back(open_gates[j]);
                    }
                }
                for (const Gate& gate : next_board.gates) {
                    if (gate.id == next_gate.id) {
                        continue;
                    }
                    std::string side_after = rotate_side(gate.side, rotation);
                    if (!side_after.empty()) {
                        next_open_gates.push_back({next_board_id, nx, ny, gate.id, side_after});
                    }
                }

                generate_layouts_dfs(
                    boards,
                    sequence,
                    index + 1,
                    occupied,
                    next_open_gates,
                    attachments,
                    rotations,
                    callback
                );

                attachments.pop_back();
                rotations.erase(next_board_id);
                occupied.erase({nx, ny});
            }
        }
    }
}

void generate_layouts(
    const std::map<std::string, Board>& boards,
    const std::vector<std::string>& sequence,
    const std::function<void(const std::map<std::string, int>&, const std::vector<Attachment>&)>& callback
) {
    if (sequence.empty()) {
        return;
    }
    std::set<std::pair<int, int>> occupied{{0, 0}};
    std::map<std::string, int> rotations{{sequence[0], 0}};
    std::vector<Attachment> attachments;
    std::vector<OpenGate> open_gates;
    for (const Gate& gate : boards.at(sequence[0]).gates) {
        std::string side_after = rotate_side(gate.side, 0);
        if (!side_after.empty()) {
            open_gates.push_back({sequence[0], 0, 0, gate.id, side_after});
        }
    }
    generate_layouts_dfs(boards, sequence, 1, occupied, open_gates, attachments, rotations, callback);
}

Graph build_combined_graph(
    const std::map<std::string, Board>& boards,
    const std::vector<std::string>& board_order,
    const std::map<std::string, int>& rotations,
    const std::vector<Attachment>& layout_attachments
) {
    Graph graph;
    graph.board_order = board_order;
    graph.rotations = rotations;
    graph.attachments = layout_attachments;
    std::unordered_set<std::string> incoming_gates;
    for (const Attachment& attachment : layout_attachments) {
        incoming_gates.insert(attachment.to);
    }
    std::map<std::string, int> board_depths;
    for (int index = 0; index < static_cast<int>(board_order.size()); ++index) {
        board_depths[board_order[index]] = index;
    }

    for (const std::string& board_id : board_order) {
        const Board& board = boards.at(board_id);
        int board_depth = board_depths.count(board_id) ? board_depths[board_id] : 0;
        int rotation = 0;
        auto rotation_it = rotations.find(board_id);
        if (rotation_it != rotations.end()) {
            rotation = rotation_it->second;
        }
        for (const Node& source : board.nodes) {
            GraphNode node;
            node.id = source.id;
            node.board_id = board.id;
            auto [x, y] = rotate_point(source.x, source.y, board.width, board.height, rotation);
            node.x = x;
            node.y = y;
            node.type = source.type;
            node.cost = source.cost;
            if (node.type == "board_gate") {
                node.cost = incoming_gates.count(node.id) ? 0 : std::max(node.cost, 1);
            }
            node.stats = source.stats;
            node.requirements = scale_requirements(source.requirements, board_depth);
            node.bonus_stats = source.bonus_stats;
            node.requirement_board_depth = board_depth;
            node.name = source.name;
            int index = static_cast<int>(graph.nodes.size());
            graph.node_index[node.id] = index;
            graph.nodes.push_back(std::move(node));
            graph.adjacency.emplace_back();
        }
    }

    auto add_edge = [&](const std::string& left, const std::string& right) {
        auto left_it = graph.node_index.find(left);
        auto right_it = graph.node_index.find(right);
        if (left_it == graph.node_index.end() || right_it == graph.node_index.end()) {
            return;
        }
        graph.adjacency[left_it->second].push_back(right_it->second);
        graph.adjacency[right_it->second].push_back(left_it->second);
    };

    for (const std::string& board_id : board_order) {
        const Board& board = boards.at(board_id);
        for (const auto& edge : board.edges) {
            add_edge(edge.first, edge.second);
        }
        for (const std::string& socket_id : board.glyph_sockets) {
            auto it = graph.node_index.find(socket_id);
            if (it != graph.node_index.end()) {
                graph.glyph_sockets.push_back(it->second);
            }
        }
        for (const std::string& legendary_id : board.legendary_nodes) {
            auto it = graph.node_index.find(legendary_id);
            if (it != graph.node_index.end()) {
                graph.legendary_nodes.push_back(it->second);
            }
        }
    }
    for (const Attachment& attachment : layout_attachments) {
        add_edge(attachment.from, attachment.to);
    }
    for (auto& row : graph.adjacency) {
        std::sort(row.begin(), row.end(), [&](int left, int right) {
            return graph.nodes[left].id < graph.nodes[right].id;
        });
        row.erase(std::unique(row.begin(), row.end()), row.end());
    }

    const Board& starter = boards.at(board_order.front());
    auto start_it = graph.node_index.find(starter.start_node);
    if (start_it == graph.node_index.end()) {
        throw std::runtime_error("starter board has no start_node: " + board_order.front());
    }
    graph.start_index = start_it->second;

    std::vector<int> order(graph.nodes.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int left, int right) {
        return graph.nodes[left].id < graph.nodes[right].id;
    });
    std::vector<int> old_to_new(graph.nodes.size(), -1);
    for (int new_index = 0; new_index < static_cast<int>(order.size()); ++new_index) {
        old_to_new[order[new_index]] = new_index;
    }

    std::vector<GraphNode> sorted_nodes;
    sorted_nodes.reserve(graph.nodes.size());
    std::vector<std::vector<int>> sorted_adjacency(graph.nodes.size());
    for (int new_index = 0; new_index < static_cast<int>(order.size()); ++new_index) {
        sorted_nodes.push_back(graph.nodes[order[new_index]]);
    }
    for (int new_index = 0; new_index < static_cast<int>(order.size()); ++new_index) {
        int old_index = order[new_index];
        for (int old_neighbor : graph.adjacency[old_index]) {
            sorted_adjacency[new_index].push_back(old_to_new[old_neighbor]);
        }
        std::sort(sorted_adjacency[new_index].begin(), sorted_adjacency[new_index].end(), [&](int left, int right) {
            return sorted_nodes[left].id < sorted_nodes[right].id;
        });
    }
    graph.nodes = std::move(sorted_nodes);
    graph.adjacency = std::move(sorted_adjacency);
    graph.node_index.clear();
    for (int index = 0; index < static_cast<int>(graph.nodes.size()); ++index) {
        graph.node_index[graph.nodes[index].id] = index;
    }
    graph.start_index = old_to_new[graph.start_index];
    for (int& index : graph.glyph_sockets) {
        index = old_to_new[index];
    }
    for (int& index : graph.legendary_nodes) {
        index = old_to_new[index];
    }
    std::sort(graph.glyph_sockets.begin(), graph.glyph_sockets.end(), [&](int left, int right) {
        return graph.nodes[left].id < graph.nodes[right].id;
    });
    std::sort(graph.legendary_nodes.begin(), graph.legendary_nodes.end(), [&](int left, int right) {
        return graph.nodes[left].id < graph.nodes[right].id;
    });
    return graph;
}

std::vector<int> reachable_nodes(const Graph& graph) {
    std::vector<unsigned char> seen(graph.nodes.size(), 0);
    std::vector<int> result;
    std::queue<int> queue;
    seen[graph.start_index] = 1;
    queue.push(graph.start_index);
    while (!queue.empty()) {
        int current = queue.front();
        queue.pop();
        result.push_back(current);
        for (int neighbor : graph.adjacency[current]) {
            if (!seen[neighbor]) {
                seen[neighbor] = 1;
                queue.push(neighbor);
            }
        }
    }
    return result;
}

double parse_glyph_requirement(const Glyph& glyph) {
    const std::string text = !glyph.bonus_text_en.empty() ? glyph.bonus_text_en : glyph.bonus_text_ru;
    static const std::regex pattern(R"(Required[^:\n]*:\s*\+?(\d+(?:\.\d+)?))", std::regex::icase);
    std::smatch match;
    if (std::regex_search(text, match, pattern)) {
        return std::stod(match[1].str());
    }
    return 25.0;
}

double parse_scaling_value_per_5(const Glyph& glyph) {
    std::string marker = "For every 5";
    size_t start = glyph.bonus_text_en.find(marker);
    if (start == std::string::npos) {
        return 0.0;
    }
    size_t percent = glyph.bonus_text_en.find('%', start + marker.size());
    if (percent == std::string::npos) {
        return 0.0;
    }
    std::string segment = glyph.bonus_text_en.substr(start + marker.size(), percent - start - marker.size());
    static const std::regex number_pattern(R"((\d+(?:\.\d+)?))");
    std::sregex_iterator it(segment.begin(), segment.end(), number_pattern);
    std::sregex_iterator end;
    std::string last_number;
    for (; it != end; ++it) {
        last_number = (*it)[1].str();
    }
    if (!last_number.empty()) {
        return std::stod(last_number);
    }
    return 0.0;
}

std::string glyph_threshold_stat(const Glyph& glyph) {
    return glyph.threshold_stats.empty() ? "" : glyph.threshold_stats.front();
}

std::string infer_glyph_bonus_stat(const Glyph& glyph, const WeightModel& weights) {
    static const std::map<std::string, std::string> tag_to_stat = {
        {"Damage", "damage"},
        {"Vulnerable", "vulnerable_damage"},
        {"Critical Strikes", "critical_strike_damage"},
        {"Critical Strike", "critical_strike_damage"},
        {"Armor", "armor"},
        {"Resistance", "resistance_all"},
        {"Resistance All", "resistance_all"},
        {"Attack Speed", "attack_speed"},
        {"Cooldown", "cooldown_reduction"},
        {"Movement", "movement_speed"},
        {"Lucky Hit", "lucky_hit_chance"},
        {"Block", "block_chance"},
        {"Thorns", "thorns"},
        {"Healing", "healing_received"},
        {"Life", "max_life"},
    };
    std::vector<std::string> candidates;
    for (const std::string& tag : glyph.skill_tag_names) {
        auto it = tag_to_stat.find(tag);
        if (it != tag_to_stat.end()) {
            candidates.push_back(it->second);
        }
    }
    if (candidates.empty()) {
        return "";
    }
    return *std::max_element(candidates.begin(), candidates.end(), [&](const std::string& left, const std::string& right) {
        double left_weight = weight_for(weights.weights, left);
        double right_weight = weight_for(weights.weights, right);
        if (std::abs(left_weight - right_weight) > 1e-12) {
            return left_weight < right_weight;
        }
        return left < right;
    });
}

double glyph_radius(const Glyph& glyph, bool legendary_glyphs) {
    const std::string key = legendary_glyphs ? "legendary" : "starting";
    auto it = glyph.radius.find(key);
    if (it != glyph.radius.end()) return it->second;
    it = glyph.radius.find("normal");
    if (it != glyph.radius.end()) return it->second;
    it = glyph.radius.find("starting");
    if (it != glyph.radius.end()) return it->second;
    return 0.0;
}

std::vector<int> nodes_in_radius(const Graph& graph, int socket_index, double radius) {
    std::vector<int> result;
    const GraphNode& socket = graph.nodes[socket_index];
    for (int index = 0; index < static_cast<int>(graph.nodes.size()); ++index) {
        const GraphNode& node = graph.nodes[index];
        if (node.board_id == socket.board_id && std::abs(node.x - socket.x) + std::abs(node.y - socket.y) <= radius) {
            result.push_back(index);
        }
    }
    return result;
}

long long radius_key(int socket_index, int glyph_index) {
    return (static_cast<long long>(socket_index) << 32) ^ static_cast<unsigned int>(glyph_index);
}

ScoringContext build_scoring_context(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    bool legendary_glyphs
) {
    ScoringContext context;
    context.glyph_info.reserve(glyphs.size());
    for (const Glyph& glyph : glyphs) {
        GlyphInfo info;
        info.threshold_stat = glyph_threshold_stat(glyph);
        info.requirement = parse_glyph_requirement(glyph);
        info.radius = glyph_radius(glyph, legendary_glyphs);
        info.bonus_stat = infer_glyph_bonus_stat(glyph, weights);
        info.scaling_value_per_5 = parse_scaling_value_per_5(glyph);
        context.glyph_info.push_back(std::move(info));
    }
    for (int socket_index : graph.glyph_sockets) {
        for (int glyph_index = 0; glyph_index < static_cast<int>(glyphs.size()); ++glyph_index) {
            context.radius_nodes[radius_key(socket_index, glyph_index)] =
                nodes_in_radius(graph, socket_index, context.glyph_info[glyph_index].radius);
        }
    }
    return context;
}

std::unordered_map<int, double> glyph_route_node_bonuses(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const ScoringContext& context
) {
    struct StatPressure {
        double demand = 0.0;
        double supply = 0.0;
        int opportunities = 0;
    };

    struct NodeAccumulator {
        double best_direct = 0.0;
        double total_direct = 0.0;
        int hits = 0;
        std::set<std::string> glyph_ids;
        std::set<int> socket_indices;
    };

    struct ThresholdCandidate {
        int node_index = -1;
        double stat_value = 0.0;
    };

    std::unordered_map<int, double> bonuses;
    if (glyphs.empty() || graph.glyph_sockets.empty()) {
        return bonuses;
    }

    bool use_all_glyphs = weights.glyph_weights.empty();
    double activation_bonus =
        std::max(weight_for(weights.weights, "glyph_bonus"), 0.0) +
        (weights.scheme_is_dict ? std::max(weight_for(weights.scheme_weights, "glyph_bonus"), 0.0) : 0.0);
    std::map<std::string, StatPressure> pressures;

    auto is_eligible_glyph = [&](int glyph_index) {
        return use_all_glyphs || weights.glyph_weights.count(glyphs[glyph_index].id) > 0;
    };

    auto radius_nodes_for = [&](int socket_index, int glyph_index) -> const std::vector<int>& {
        static const std::vector<int> empty;
        auto it = context.radius_nodes.find(radius_key(socket_index, glyph_index));
        return it == context.radius_nodes.end() ? empty : it->second;
    };

    for (int socket_index : graph.glyph_sockets) {
        for (int glyph_index = 0; glyph_index < static_cast<int>(glyphs.size()); ++glyph_index) {
            if (!is_eligible_glyph(glyph_index)) {
                continue;
            }
            const GlyphInfo& info = context.glyph_info[glyph_index];
            if (info.threshold_stat.empty() || info.requirement <= 0.0) {
                continue;
            }
            double supply = 0.0;
            for (int node_index : radius_nodes_for(socket_index, glyph_index)) {
                if (node_index == socket_index) {
                    continue;
                }
                supply += std::max(weight_for(graph.nodes[node_index].stats, info.threshold_stat), 0.0);
            }
            if (supply <= 0.0) {
                continue;
            }
            double target = std::min(supply, std::max(info.requirement, info.requirement * weights.glyph_route.fill_target));
            StatPressure& pressure = pressures[info.threshold_stat];
            pressure.demand += target;
            pressure.supply += supply;
            pressure.opportunities += 1;
        }
    }

    std::vector<NodeAccumulator> accumulators(graph.nodes.size());

    for (int socket_index : graph.glyph_sockets) {
        const GraphNode& socket = graph.nodes[socket_index];
        for (int glyph_index = 0; glyph_index < static_cast<int>(glyphs.size()); ++glyph_index) {
            if (!is_eligible_glyph(glyph_index)) {
                continue;
            }
            const Glyph& glyph = glyphs[glyph_index];
            const GlyphInfo& info = context.glyph_info[glyph_index];
            if (info.threshold_stat.empty() || info.requirement <= 0.0) {
                continue;
            }

            std::vector<ThresholdCandidate> threshold_nodes;
            double available_stat = 0.0;
            for (int node_index : radius_nodes_for(socket_index, glyph_index)) {
                if (node_index == socket_index) {
                    continue;
                }
                const GraphNode& node = graph.nodes[node_index];
                double stat_value = weight_for(node.stats, info.threshold_stat);
                if (stat_value > 0.0) {
                    threshold_nodes.push_back({node_index, stat_value});
                    available_stat += stat_value;
                }
            }
            if (threshold_nodes.empty() || available_stat <= 0.0) {
                continue;
            }

            std::sort(threshold_nodes.begin(), threshold_nodes.end(), [&](const ThresholdCandidate& left, const ThresholdCandidate& right) {
                const GraphNode& left_node = graph.nodes[left.node_index];
                const GraphNode& right_node = graph.nodes[right.node_index];
                int left_distance = std::abs(left_node.x - socket.x) + std::abs(left_node.y - socket.y);
                int right_distance = std::abs(right_node.x - socket.x) + std::abs(right_node.y - socket.y);
                if (left_distance != right_distance) return left_distance < right_distance;
                double left_score = node_base_score(left_node, weights);
                double right_score = node_base_score(right_node, weights);
                if (std::abs(left_score - right_score) > 1e-12) return left_score > right_score;
                if (std::abs(left.stat_value - right.stat_value) > 1e-12) return left.stat_value > right.stat_value;
                return left_node.id < right_node.id;
            });

            double preference_bonus = std::max(weight_for(weights.glyph_weights, glyph.id), 0.0);
            double activation_per_stat = (activation_bonus + preference_bonus) / info.requirement;
            double scaling_weight = info.bonus_stat.empty() ? 0.0 : std::max(weight_for(weights.weights, info.bonus_stat), 0.0);
            double scaling_per_stat = std::max(info.scaling_value_per_5 * scaling_weight / 5.0, 0.0);
            if (activation_per_stat <= 0.0 && scaling_per_stat <= 0.0) {
                continue;
            }

            double scarcity_multiplier = 1.0;
            auto pressure_it = pressures.find(info.threshold_stat);
            if (pressure_it != pressures.end() && pressure_it->second.supply > 0.0) {
                double demand_ratio = pressure_it->second.demand / pressure_it->second.supply;
                double pressure_boost = std::clamp(demand_ratio - 0.80, 0.0, 2.0) * weights.glyph_route.scarcity;
                double opportunity_boost = std::log1p(static_cast<double>(std::max(pressure_it->second.opportunities - 1, 0))) *
                    weights.glyph_route.synergy * 0.05;
                scarcity_multiplier += pressure_boost + opportunity_boost;
            }

            double expected_fill = std::min(
                available_stat,
                std::max(info.requirement, info.requirement * weights.glyph_route.fill_target)
            );
            double accumulated_stat = 0.0;
            for (const ThresholdCandidate& candidate : threshold_nodes) {
                double remaining_fill = std::max(expected_fill - accumulated_stat, 0.0);
                double selected_stat = std::min(candidate.stat_value, remaining_fill);
                if (selected_stat <= 0.0 && weights.glyph_route.future <= 0.0) {
                    accumulated_stat += candidate.stat_value;
                    continue;
                }

                double activation_gap = std::max(info.requirement - std::min(accumulated_stat, info.requirement), 0.0);
                double activation_stat = std::min(selected_stat, activation_gap);
                double future_stat = (candidate.stat_value - selected_stat) * weights.glyph_route.future * 0.15;
                double scaling_stat = selected_stat + future_stat;
                double direct =
                    activation_stat * activation_per_stat * weights.glyph_route.activation +
                    scaling_stat * scaling_per_stat * weights.glyph_route.scaling;
                direct *= GLYPH_ROUTE_BONUS_FACTOR * scarcity_multiplier;
                if (direct > 0.0) {
                    NodeAccumulator& accumulator = accumulators[candidate.node_index];
                    accumulator.best_direct = std::max(accumulator.best_direct, direct);
                    accumulator.total_direct += direct;
                    accumulator.hits += 1;
                    accumulator.glyph_ids.insert(glyph.id);
                    accumulator.socket_indices.insert(socket_index);
                }
                accumulated_stat += candidate.stat_value;
            }
        }
    }

    for (int node_index = 0; node_index < static_cast<int>(accumulators.size()); ++node_index) {
        const NodeAccumulator& accumulator = accumulators[node_index];
        if (accumulator.total_direct <= 0.0) {
            continue;
        }
        double shared_value = std::max(accumulator.total_direct - accumulator.best_direct, 0.0);
        double shared_glyphs = static_cast<double>(std::max<int>(static_cast<int>(accumulator.glyph_ids.size()) - 1, 0));
        double shared_sockets = static_cast<double>(std::max<int>(static_cast<int>(accumulator.socket_indices.size()) - 1, 0));
        double future_bonus = shared_value * weights.glyph_route.future;
        double synergy_scale = std::min(shared_glyphs + shared_sockets * 0.5, 4.0) / 4.0;
        double synergy_bonus = shared_value * weights.glyph_route.synergy * synergy_scale;
        double candidate_bonus = accumulator.best_direct + future_bonus + synergy_bonus;
        if (accumulator.hits > 1 && weights.glyph_route.synergy > 0.0) {
            candidate_bonus *= 1.0 + std::min(accumulator.hits - 1, 6) * weights.glyph_route.synergy * 0.02;
        }

        const GraphNode& node = graph.nodes[node_index];
        double base_limit = std::max(node_base_score(node, weights), 1.0);
        double expected_limit = std::max(base_limit, accumulator.best_direct * 0.35);
        double route_hint_limit = expected_limit * weights.glyph_route.max_bonus_multiplier;
        bonuses[node_index] = std::min(candidate_bonus, route_hint_limit);
    }

    return bonuses;
}

std::vector<int> candidate_targets(
    const Graph& graph,
    const WeightModel& weights,
    int candidate_limit,
    const std::unordered_map<int, double>& route_bonuses
) {
    struct TargetCandidate {
        int index = -1;
        double score = 0.0;
        std::string type;
    };
    std::vector<TargetCandidate> candidates;
    for (int index : reachable_nodes(graph)) {
        const GraphNode& node = graph.nodes[index];
        double score = route_node_score(node, weights, &route_bonuses, index);
        if (node.type == "rare" || node.type == "legendary" || node.type == "glyph_socket" || score > 0.0) {
            candidates.push_back({index, score, node.type});
        }
    }
    std::sort(candidates.begin(), candidates.end(), [&](const TargetCandidate& left, const TargetCandidate& right) {
        if (std::abs(left.score - right.score) > 1e-12) return left.score > right.score;
        return graph.nodes[left.index].id < graph.nodes[right.index].id;
    });
    if (candidate_limit > 0 && static_cast<int>(candidates.size()) > candidate_limit) {
        int attribute_budget = candidate_limit >= 6 ? std::max(candidate_limit / 6, 1) : 0;
        int priority_budget = candidate_limit - attribute_budget;
        std::vector<TargetCandidate> priority;
        std::vector<TargetCandidate> attributes;
        for (const TargetCandidate& candidate : candidates) {
            if (candidate.type == "normal") attributes.push_back(candidate);
            else priority.push_back(candidate);
        }
        std::vector<TargetCandidate> limited;
        for (int i = 0; i < std::min(priority_budget, static_cast<int>(priority.size())); ++i) {
            limited.push_back(priority[i]);
        }
        int remaining = candidate_limit - static_cast<int>(limited.size());
        for (int i = 0; i < std::min(remaining, static_cast<int>(attributes.size())); ++i) {
            limited.push_back(attributes[i]);
        }
        if (static_cast<int>(limited.size()) < candidate_limit) {
            std::unordered_set<int> used;
            for (const auto& item : limited) used.insert(item.index);
            for (const auto& item : candidates) {
                if (!used.count(item.index)) {
                    limited.push_back(item);
                    if (static_cast<int>(limited.size()) == candidate_limit) break;
                }
            }
        }
        candidates = std::move(limited);
    }
    std::vector<int> result;
    result.reserve(candidates.size());
    for (const TargetCandidate& candidate : candidates) {
        result.push_back(candidate.index);
    }
    return result;
}

void expand_free_nodes(const RouteInput& input, std::vector<unsigned char>& selected, std::queue<int>& queue) {
    while (!queue.empty()) {
        int current = queue.front();
        queue.pop();
        for (int neighbor : input.adjacency[current]) {
            if (!selected[neighbor] && input.costs[neighbor] == 0) {
                selected[neighbor] = 1;
                queue.push(neighbor);
            }
        }
    }
}

std::vector<int> shortest_path_tree(const RouteInput& input, const std::vector<unsigned char>& selected) {
    std::vector<int> previous(input.node_ids.size(), -2);
    std::queue<int> queue;
    for (size_t index = 0; index < selected.size(); ++index) {
        if (selected[index]) {
            previous[index] = -1;
            queue.push(static_cast<int>(index));
        }
    }
    while (!queue.empty()) {
        int current = queue.front();
        queue.pop();
        for (int neighbor : input.adjacency[current]) {
            if (previous[neighbor] != -2) {
                continue;
            }
            previous[neighbor] = current;
            queue.push(neighbor);
        }
    }
    return previous;
}

std::vector<int> path_from_tree(const std::vector<int>& previous, int target) {
    if (target < 0 || static_cast<size_t>(target) >= previous.size() || previous[target] == -2) {
        return {};
    }
    std::vector<int> path;
    int current = target;
    while (current != -1) {
        path.push_back(current);
        current = previous[current];
    }
    std::reverse(path.begin(), path.end());
    return path;
}

bool better_candidate(const Candidate& candidate, const Candidate& best, const RouteInput& input) {
    if (!best.valid) {
        return true;
    }
    constexpr double eps = 1e-12;
    if (candidate.ratio > best.ratio + eps) return true;
    if (std::abs(candidate.ratio - best.ratio) <= eps) {
        if (candidate.gain > best.gain + eps) return true;
        if (std::abs(candidate.gain - best.gain) <= eps) {
            if (candidate.negative_cost > best.negative_cost) return true;
            if (candidate.negative_cost == best.negative_cost) {
                return input.node_ids[candidate.target] > input.node_ids[best.target];
            }
        }
    }
    return false;
}

void consider_targets(
    const RouteInput& input,
    const std::vector<unsigned char>& selected,
    int points_used,
    const std::vector<int>& previous,
    const std::vector<int>& target_pool,
    Candidate& best
) {
    for (int target : target_pool) {
        if (selected[target]) continue;
        std::vector<int> path = path_from_tree(previous, target);
        if (path.empty()) continue;
        std::vector<int> new_nodes;
        int cost = 0;
        double gain = 0.0;
        for (int node : path) {
            if (!selected[node]) {
                new_nodes.push_back(node);
                cost += input.costs[node];
                gain += input.scores[node];
            }
        }
        if (cost <= 0 || points_used + cost > input.points) continue;
        if (gain <= 0.0 && input.types[target] != TYPE_GLYPH_SOCKET && input.types[target] != TYPE_LEGENDARY) continue;

        Candidate candidate;
        candidate.valid = true;
        candidate.ratio = gain / static_cast<double>(cost);
        candidate.gain = gain;
        candidate.negative_cost = -cost;
        candidate.target = target;
        candidate.path = std::move(path);
        candidate.new_nodes = std::move(new_nodes);
        if (better_candidate(candidate, best, input)) {
            best = std::move(candidate);
        }
    }
}

std::vector<int> ordered_targets(const RouteInput& input, int seed) {
    std::vector<int> targets = input.targets;
    if (seed < 0) return targets;
    auto it = std::find(targets.begin(), targets.end(), seed);
    if (it == targets.end()) return targets;
    targets.erase(it);
    targets.insert(targets.begin(), seed);
    return targets;
}

RouteOutput run_greedy_route(const RouteInput& input, const std::vector<int>& target_pool) {
    RouteOutput output;
    output.selected.assign(input.node_ids.size(), 0);
    output.selected[static_cast<size_t>(input.start_index)] = 1;
    {
        std::queue<int> free_queue;
        free_queue.push(input.start_index);
        expand_free_nodes(input, output.selected, free_queue);
    }
    int points_used = 0;
    for (size_t index = 0; index < output.selected.size(); ++index) {
        if (output.selected[index]) {
            points_used += input.costs[index];
        }
    }
    while (points_used < input.points) {
        Candidate best;
        std::vector<int> previous = shortest_path_tree(input, output.selected);
        consider_targets(input, output.selected, points_used, previous, target_pool, best);
        if (!best.valid) {
            std::vector<unsigned char> target_set(input.node_ids.size(), 0);
            for (int target : target_pool) target_set[target] = 1;
            std::vector<int> fallback_targets;
            fallback_targets.reserve(input.node_ids.size());
            for (size_t index = 0; index < previous.size(); ++index) {
                if (previous[index] != -2 && !target_set[index]) {
                    fallback_targets.push_back(static_cast<int>(index));
                }
            }
            consider_targets(input, output.selected, points_used, previous, fallback_targets, best);
        }
        if (!best.valid) break;

        std::vector<int> added_nodes = best.new_nodes;
        for (int node : best.new_nodes) output.selected[node] = 1;
        std::queue<int> expansion_queue;
        for (int node : best.new_nodes) expansion_queue.push(node);
        while (!expansion_queue.empty()) {
            int current = expansion_queue.front();
            expansion_queue.pop();
            for (int neighbor : input.adjacency[current]) {
                if (!output.selected[neighbor] && input.costs[neighbor] == 0) {
                    output.selected[neighbor] = 1;
                    added_nodes.push_back(neighbor);
                    expansion_queue.push(neighbor);
                }
            }
        }
        int cost = 0;
        for (int node : added_nodes) cost += input.costs[node];
        points_used += cost;

        RouteStep step;
        step.target = best.target;
        step.path = std::move(best.path);
        step.added_nodes = std::move(added_nodes);
        step.cost = cost;
        step.gain = best.gain;
        step.points_used = points_used;
        output.steps.push_back(std::move(step));
    }
    return output;
}

RouteInput build_route_input(
    const Graph& graph,
    const WeightModel& weights,
    int points,
    const std::vector<int>& targets,
    const std::unordered_map<int, double>& route_bonuses
) {
    RouteInput input;
    input.points = points;
    input.start_index = graph.start_index;
    input.targets = targets;
    int size = static_cast<int>(graph.nodes.size());
    input.node_ids.reserve(size);
    input.costs.reserve(size);
    input.scores.reserve(size);
    input.types.reserve(size);
    input.adjacency = graph.adjacency;
    for (int index = 0; index < size; ++index) {
        const GraphNode& node = graph.nodes[index];
        input.node_ids.push_back(node.id);
        input.costs.push_back(node.cost);
        input.scores.push_back(route_node_score(node, weights, &route_bonuses, index));
        input.types.push_back(node_type_code(node.type));
    }
    return input;
}

bool requirements_met(const std::map<std::string, double>& requirements, const std::map<std::string, double>& totals) {
    for (const auto& [stat, required] : requirements) {
        if (weight_for(totals, stat) < required) {
            return false;
        }
    }
    return true;
}

GlyphEvaluation evaluate_glyph(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const ScoringContext& context,
    const std::vector<unsigned char>& selected,
    int socket_index,
    int glyph_index
) {
    const Glyph& glyph = glyphs[glyph_index];
    const GlyphInfo& info = context.glyph_info[glyph_index];
    double stat_in_radius = 0.0;
    if (!info.threshold_stat.empty()) {
        auto it = context.radius_nodes.find(radius_key(socket_index, glyph_index));
        if (it != context.radius_nodes.end()) {
            for (int node_index : it->second) {
                if (selected[node_index]) {
                    stat_in_radius += weight_for(graph.nodes[node_index].stats, info.threshold_stat);
                }
            }
        }
    }
    bool requirement_met = !info.threshold_stat.empty() && stat_in_radius >= info.requirement;
    int increments = static_cast<int>(std::floor(stat_in_radius / 5.0));
    double score = 0.0;
    if (!info.bonus_stat.empty()) {
        score += increments * info.scaling_value_per_5 * weight_for(weights.weights, info.bonus_stat);
    }
    if (requirement_met) {
        double scheme_bonus = weights.scheme_is_dict ? weight_for(weights.scheme_weights, "glyph_bonus") : 0.0;
        score += weight_for(weights.weights, "glyph_bonus") + scheme_bonus;
    }
    if (weights.glyph_weights.count(glyph.id)) {
        double preferred = weight_for(weights.glyph_weights, glyph.id);
        score += (requirement_met || info.threshold_stat.empty()) ? preferred : preferred * UNMET_PREFERRED_GLYPH_WEIGHT_FACTOR;
    }
    GlyphEvaluation evaluation;
    evaluation.glyph_index = glyph_index;
    evaluation.socket_index = socket_index;
    evaluation.score = score;
    evaluation.stat_in_radius = stat_in_radius;
    evaluation.requirement = info.requirement;
    evaluation.requirement_met = requirement_met;
    evaluation.radius = info.radius;
    evaluation.bonus_stat = info.bonus_stat;
    evaluation.scaling_value_per_5 = info.scaling_value_per_5;
    if (!info.threshold_stat.empty() && !requirement_met) {
        evaluation.warnings.push_back(
            glyph.id + " requirement not met at " + graph.nodes[socket_index].id + ": " +
            std::to_string(stat_in_radius) + "/" + std::to_string(info.requirement) + " " + info.threshold_stat
        );
    }
    return evaluation;
}

bool better_glyph_tuple(
    const std::tuple<double, int, std::vector<std::string>>& candidate,
    const std::tuple<double, int, std::vector<std::string>>& best
) {
    constexpr double eps = 1e-12;
    if (std::get<0>(candidate) > std::get<0>(best) + eps) return true;
    if (std::abs(std::get<0>(candidate) - std::get<0>(best)) <= eps) {
        if (std::get<1>(candidate) > std::get<1>(best)) return true;
        if (std::get<1>(candidate) == std::get<1>(best)) {
            return std::get<2>(candidate) > std::get<2>(best);
        }
    }
    return false;
}

std::vector<GlyphEvaluation> assign_glyphs(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const ScoringContext& context,
    const std::vector<unsigned char>& selected
) {
    std::vector<int> sockets;
    for (int socket_index : graph.glyph_sockets) {
        if (selected[socket_index]) sockets.push_back(socket_index);
    }
    if (sockets.empty() || glyphs.empty()) {
        return {};
    }

    std::vector<std::vector<GlyphEvaluation>> candidates_by_socket;
    std::unordered_set<std::string> preferred;
    for (const auto& [glyph_id, _] : weights.glyph_weights) {
        preferred.insert(glyph_id);
    }
    for (int socket_index : sockets) {
        std::vector<GlyphEvaluation> evaluations;
        for (int glyph_index = 0; glyph_index < static_cast<int>(glyphs.size()); ++glyph_index) {
            evaluations.push_back(evaluate_glyph(graph, glyphs, weights, context, selected, socket_index, glyph_index));
        }
        std::sort(evaluations.begin(), evaluations.end(), [&](const GlyphEvaluation& left, const GlyphEvaluation& right) {
            if (std::abs(left.score - right.score) > 1e-12) return left.score > right.score;
            if (left.requirement_met != right.requirement_met) return left.requirement_met > right.requirement_met;
            return glyphs[left.glyph_index].id < glyphs[right.glyph_index].id;
        });
        std::vector<GlyphEvaluation> candidates;
        for (const GlyphEvaluation& evaluation : evaluations) {
            if (preferred.count(glyphs[evaluation.glyph_index].id) || static_cast<int>(candidates.size()) < MAX_GLYPH_CANDIDATES_PER_SOCKET) {
                candidates.push_back(evaluation);
            }
        }
        candidates_by_socket.push_back(std::move(candidates));
    }

    std::vector<std::string> unique_glyphs;
    for (const auto& candidates : candidates_by_socket) {
        for (const GlyphEvaluation& evaluation : candidates) {
            unique_glyphs.push_back(glyphs[evaluation.glyph_index].id);
        }
    }
    std::sort(unique_glyphs.begin(), unique_glyphs.end());
    unique_glyphs.erase(std::unique(unique_glyphs.begin(), unique_glyphs.end()), unique_glyphs.end());
    std::map<std::string, int> glyph_bit_index;
    for (int index = 0; index < static_cast<int>(unique_glyphs.size()); ++index) {
        glyph_bit_index[unique_glyphs[index]] = index;
    }

    struct DpValue {
        double score = 0.0;
        int requirements = 0;
        std::vector<std::string> glyph_key;
        std::vector<GlyphEvaluation> chosen;
        bool valid = false;
    };
    std::map<std::pair<int, unsigned long long>, DpValue> memo;
    std::function<DpValue(int, unsigned long long)> search = [&](int socket_pos, unsigned long long used_mask) -> DpValue {
        if (socket_pos >= static_cast<int>(candidates_by_socket.size())) {
            DpValue value;
            value.valid = true;
            return value;
        }
        auto key = std::make_pair(socket_pos, used_mask);
        auto memo_it = memo.find(key);
        if (memo_it != memo.end()) {
            return memo_it->second;
        }
        DpValue best;
        for (const GlyphEvaluation& evaluation : candidates_by_socket[socket_pos]) {
            const std::string& glyph_id = glyphs[evaluation.glyph_index].id;
            unsigned long long bit = 1ull << glyph_bit_index[glyph_id];
            if (used_mask & bit) {
                continue;
            }
            DpValue tail = search(socket_pos + 1, used_mask | bit);
            DpValue candidate;
            candidate.score = evaluation.score + tail.score;
            candidate.requirements = static_cast<int>(evaluation.requirement_met) + tail.requirements;
            candidate.glyph_key.push_back(glyph_id);
            candidate.glyph_key.insert(candidate.glyph_key.end(), tail.glyph_key.begin(), tail.glyph_key.end());
            candidate.chosen.push_back(evaluation);
            candidate.chosen.insert(candidate.chosen.end(), tail.chosen.begin(), tail.chosen.end());
            candidate.valid = true;
            auto candidate_tuple = std::make_tuple(candidate.score, candidate.requirements, candidate.glyph_key);
            auto best_tuple = std::make_tuple(best.score, best.requirements, best.glyph_key);
            if (!best.valid || better_glyph_tuple(candidate_tuple, best_tuple)) {
                best = std::move(candidate);
            }
        }
        if (!best.valid) {
            best.valid = true;
        }
        memo[key] = best;
        return best;
    };
    return search(0, 0).chosen;
}

double round4(double value) {
    return std::round(value * 10000.0) / 10000.0;
}

json glyph_route_tuning_json(const GlyphRouteTuning& tuning) {
    return {
        {"activation", round4(tuning.activation)},
        {"scaling", round4(tuning.scaling)},
        {"future", round4(tuning.future)},
        {"synergy", round4(tuning.synergy)},
        {"scarcity", round4(tuning.scarcity)},
        {"fill_target", round4(tuning.fill_target)},
        {"max_bonus_multiplier", round4(tuning.max_bonus_multiplier)}
    };
}

std::string format_value(double value);

json map_to_json_object(const std::map<std::string, double>& values, bool rounded = false) {
    json payload = json::object();
    for (const auto& [key, value] : values) {
        payload[key] = rounded ? round4(value) : value;
    }
    return payload;
}

std::vector<int> route_selected_indices(const std::vector<unsigned char>& selected) {
    std::vector<int> indices;
    indices.reserve(selected.size());
    for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
        if (selected[index]) {
            indices.push_back(index);
        }
    }
    return indices;
}

int route_points_used(const Graph& graph, const std::vector<unsigned char>& selected) {
    int points = 0;
    for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
        if (selected[index]) {
            points += graph.nodes[index].cost;
        }
    }
    return points;
}

double route_score_value(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const std::map<std::string, double>& starting_stats,
    const ScoringContext& context,
    const std::vector<unsigned char>& selected,
    int points_limit
) {
    std::vector<int> selected_indices = route_selected_indices(selected);

    std::map<std::string, double> totals;
    for (int index : selected_indices) {
        for (const auto& [stat, value] : graph.nodes[index].stats) {
            totals[stat] += value;
        }
    }
    std::map<std::string, double> effective_totals = totals;
    for (const auto& [stat, value] : starting_stats) {
        effective_totals[stat] += value;
    }

    double base_score = weighted_stats_score(totals, weights);
    double type_bonus = 0.0;
    std::vector<unsigned char> active_node_bonus(graph.nodes.size(), 0);
    std::map<std::string, double> bonus_totals;
    for (int index : selected_indices) {
        type_bonus += priority_for_type(graph.nodes[index], weights);
    }

    // Gated rare/legendary node bonuses.
    // These only contribute if the node is taken AND the (scaled) requirements are met
    // using the final effective totals (including starting stats + all selected nodes).
    // This matches in-game behavior: the attributes on such nodes are locked behind reqs.
    bool changed = true;
    while (changed) {
        changed = false;
        for (int index : selected_indices) {
            const GraphNode& node = graph.nodes[index];
            if (active_node_bonus[index] || (node.type != "rare" && node.type != "legendary") || node.requirements.empty()) {
                continue;
            }
            if (!requirements_met(node.requirements, effective_totals)) {
                continue;
            }
            active_node_bonus[index] = 1;
            changed = true;
            for (const auto& [stat, value] : node.bonus_stats) {
                bonus_totals[stat] += value;
                effective_totals[stat] += value;
            }
        }
    }

    double glyph_score = 0.0;
    for (const GlyphEvaluation& evaluation : assign_glyphs(graph, glyphs, weights, context, selected)) {
        glyph_score += evaluation.score;
    }

    double penalties = 0.0;
    for (const auto& [stat, minimum] : weights.minimums) {
        double current = weight_for(effective_totals, stat);
        if (current < minimum) {
            double shortfall = minimum - current;
            double penalty_weight = std::abs(weight_for(weights.weights, stat, 1.0));
            if (penalty_weight == 0.0) penalty_weight = 1.0;
            penalties += shortfall * penalty_weight;
        }
    }
    int points_used = route_points_used(graph, selected);
    if (points_used > points_limit) {
        penalties += (points_used - points_limit) * 1000000.0;
    }

    return base_score + type_bonus + weighted_stats_score(bonus_totals, weights) + glyph_score - penalties;
}

ScoredRoute score_route(
    const Graph& graph,
    const std::map<std::string, Board>& boards,
    const std::vector<std::string>& sequence,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const std::map<std::string, double>& starting_stats,
    const ScoringContext& context,
    const RouteOutput& route,
    int points_limit,
    bool include_route_steps
) {
    std::vector<int> selected_indices;
    selected_indices.reserve(route.selected.size());
    for (int index = 0; index < static_cast<int>(route.selected.size()); ++index) {
        if (route.selected[index]) selected_indices.push_back(index);
    }

    std::map<std::string, double> totals;
    for (int index : selected_indices) {
        for (const auto& [stat, value] : graph.nodes[index].stats) {
            totals[stat] += value;
        }
    }
    std::map<std::string, double> effective_totals = totals;
    for (const auto& [stat, value] : starting_stats) {
        effective_totals[stat] += value;
    }

    double base_score = weighted_stats_score(totals, weights);
    double type_bonus = 0.0;
    std::vector<unsigned char> active_node_bonus(graph.nodes.size(), 0);
    std::map<std::string, double> bonus_totals;
    for (int index : selected_indices) {
        type_bonus += priority_for_type(graph.nodes[index], weights);
    }

    // Gated rare/legendary node bonuses.
    // These only contribute if the node is taken AND the (scaled) requirements are met
    // using the final effective totals (including starting stats + all selected nodes).
    // This matches in-game behavior: the attributes on such nodes are locked behind reqs.
    bool changed = true;
    while (changed) {
        changed = false;
        for (int index : selected_indices) {
            const GraphNode& node = graph.nodes[index];
            if (active_node_bonus[index] || (node.type != "rare" && node.type != "legendary") || node.requirements.empty()) {
                continue;
            }
            if (!requirements_met(node.requirements, effective_totals)) {
                continue;
            }
            active_node_bonus[index] = 1;
            changed = true;
            for (const auto& [stat, value] : node.bonus_stats) {
                bonus_totals[stat] += value;
                effective_totals[stat] += value;
            }
        }
    }

    double gated_node_bonus_score = weighted_stats_score(bonus_totals, weights);
    double node_bonus = type_bonus + gated_node_bonus_score;
    std::vector<std::string> activated;
    std::vector<std::string> requirement_warnings;
    json requirement_payload = json::array();
    for (int index : selected_indices) {
        const GraphNode& node = graph.nodes[index];
        if ((node.type != "rare" && node.type != "legendary") || node.requirements.empty()) {
            continue;
        }
        bool met = active_node_bonus[index] != 0 || requirements_met(node.requirements, effective_totals);
        if (met) {
            activated.push_back(node.id);
        }

        json item;
        item["node"] = node.id;
        item["board"] = node.board_id;
        item["type"] = node.type;
        item["name"] = node.name;
        item["met"] = met;
        item["board_depth"] = node.requirement_board_depth;
        item["requirement_scale"] = round4(rare_requirement_scale_for_board_depth(node.requirement_board_depth));
        item["requirements"] = map_to_json_object(node.requirements);
        item["bonus_stats"] = map_to_json_object(node.bonus_stats);
        item["bonus_score"] = met ? round4(weighted_stats_score(node.bonus_stats, weights)) : 0.0;
        json effective = json::object();
        json missing = json::object();
        std::vector<std::string> missing_bits;
        for (const auto& [stat, required] : node.requirements) {
            double current = weight_for(effective_totals, stat);
            effective[stat] = round4(current);
            double shortfall = std::max(0.0, required - current);
            missing[stat] = round4(shortfall);
            if (shortfall > 0.0) {
                missing_bits.push_back(stat + " " + format_value(current) + "/" + format_value(required));
            }
        }
        item["effective_totals"] = std::move(effective);
        item["missing"] = std::move(missing);
        if (!met && !missing_bits.empty()) {
            std::ostringstream warning;
            warning << localized_name(node.name, node.id) << " (" << node.id << ") requirement not met: ";
            for (size_t pos = 0; pos < missing_bits.size(); ++pos) {
                if (pos) warning << ", ";
                warning << missing_bits[pos];
            }
            item["warning"] = warning.str();
            requirement_warnings.push_back(warning.str());
        }
        requirement_payload.push_back(std::move(item));
    }

    std::vector<GlyphEvaluation> assigned_glyphs = assign_glyphs(graph, glyphs, weights, context, route.selected);
    double glyph_score = 0.0;
    std::vector<std::string> warnings;
    warnings.insert(warnings.end(), requirement_warnings.begin(), requirement_warnings.end());
    json glyph_payload = json::array();
    std::unordered_map<int, GlyphEvaluation> glyph_by_socket;
    for (const GlyphEvaluation& evaluation : assigned_glyphs) {
        const Glyph& glyph = glyphs[evaluation.glyph_index];
        glyph_score += evaluation.score;
        glyph_by_socket[evaluation.socket_index] = evaluation;
        for (const std::string& warning : evaluation.warnings) {
            warnings.push_back(warning);
        }
        json item;
        item["socket"] = graph.nodes[evaluation.socket_index].id;
        item["glyph"] = glyph.id;
        item["name"] = glyph.name;
        item["score"] = round4(evaluation.score);
        item["radius"] = evaluation.radius;
        item["requirement_met"] = evaluation.requirement_met;
        item["stat_in_radius"] = evaluation.stat_in_radius;
        item["requirement"] = evaluation.requirement;
        item["bonus_stat"] = evaluation.bonus_stat.empty() ? json(nullptr) : json(evaluation.bonus_stat);
        item["scaling_value_per_5"] = evaluation.scaling_value_per_5;
        glyph_payload.push_back(std::move(item));
    }

    double penalties = 0.0;
    for (const auto& [stat, minimum] : weights.minimums) {
        double current = weight_for(effective_totals, stat);
        if (current < minimum) {
            double shortfall = minimum - current;
            double penalty_weight = std::abs(weight_for(weights.weights, stat, 1.0));
            if (penalty_weight == 0.0) penalty_weight = 1.0;
            penalties += shortfall * penalty_weight;
            warnings.push_back(stat + " minimum not reached");
        }
    }
    int points_used = 0;
    for (int index : selected_indices) {
        points_used += graph.nodes[index].cost;
    }
    if (points_used > points_limit) {
        penalties += (points_used - points_limit) * 1000000.0;
        warnings.push_back("points limit exceeded");
    }
    double score = base_score + node_bonus + glyph_score - penalties;

    std::unordered_set<std::string> used_boards;
    for (int index : selected_indices) {
        used_boards.insert(graph.nodes[index].board_id);
    }
    std::unordered_set<int> selected_set(selected_indices.begin(), selected_indices.end());

    std::map<std::string, int> board_points_before;
    std::map<std::string, int> board_points_after_first_node;
    std::map<std::string, std::string> board_first_node;
    std::map<std::string, int> selected_cost_by_board;
    std::map<std::string, std::vector<int>> selected_indices_by_board;
    for (int index : selected_indices) {
        const GraphNode& node = graph.nodes[index];
        selected_cost_by_board[node.board_id] += node.cost;
        selected_indices_by_board[node.board_id].push_back(index);
    }
    std::map<std::string, int> incoming_entry_node_by_board;
    for (const Attachment& attachment : graph.attachments) {
        auto to_it = graph.node_index.find(attachment.to);
        if (to_it == graph.node_index.end() || !selected_set.count(to_it->second)) {
            continue;
        }
        const GraphNode& node = graph.nodes[to_it->second];
        incoming_entry_node_by_board[node.board_id] = to_it->second;
    }
    int running_points_by_board = 0;
    for (const std::string& board_id : sequence) {
        if (!used_boards.count(board_id)) {
            continue;
        }
        board_points_before[board_id] = running_points_by_board;
        int first_node_index = -1;
        if (graph.start_index >= 0 && graph.nodes[graph.start_index].board_id == board_id && selected_set.count(graph.start_index)) {
            first_node_index = graph.start_index;
        } else if (incoming_entry_node_by_board.count(board_id)) {
            first_node_index = incoming_entry_node_by_board[board_id];
        } else {
            auto selected_it = selected_indices_by_board.find(board_id);
            if (selected_it != selected_indices_by_board.end() && !selected_it->second.empty()) {
                first_node_index = *std::min_element(selected_it->second.begin(), selected_it->second.end(), [&](int left, int right) {
                    return graph.nodes[left].id < graph.nodes[right].id;
                });
            }
        }
        if (first_node_index >= 0) {
            board_first_node[board_id] = graph.nodes[first_node_index].id;
            board_points_after_first_node[board_id] = running_points_by_board + graph.nodes[first_node_index].cost;
        } else {
            board_points_after_first_node[board_id] = running_points_by_board;
        }
        running_points_by_board += selected_cost_by_board[board_id];
    }

    json boards_payload = json::array();
    for (const std::string& board_id : sequence) {
        if (!used_boards.count(board_id)) {
            continue;
        }
        const Board& board = boards.at(board_id);
        json item;
        item["id"] = board_id;
        item["name"] = board.name;
        int rotation = 0;
        auto rotation_it = graph.rotations.find(board_id);
        if (rotation_it != graph.rotations.end()) rotation = rotation_it->second;
        item["rotation"] = rotation;
        item["rotation_turns"] = (rotation % 360) / 90;
        if (board_points_before.count(board_id)) {
            int before = board_points_before[board_id];
            item["points_used_before_board"] = before;
            item["points_remaining_before_board"] = points_limit - before;
            item["points_used_after_first_board_node"] = board_points_after_first_node[board_id];
            item["points_remaining_after_first_board_node"] = points_limit - board_points_after_first_node[board_id];
            item["first_selected_node"] = board_first_node[board_id];
        }
        item["glyph_socket"] = nullptr;
        item["glyph"] = nullptr;
        item["glyph_name"] = nullptr;
        for (const std::string& socket_id : board.glyph_sockets) {
            auto socket_it = graph.node_index.find(socket_id);
            if (socket_it != graph.node_index.end() && selected_set.count(socket_it->second)) {
                item["glyph_socket"] = socket_id;
                auto glyph_it = glyph_by_socket.find(socket_it->second);
                if (glyph_it != glyph_by_socket.end()) {
                    const Glyph& glyph = glyphs[glyph_it->second.glyph_index];
                    item["glyph"] = glyph.id;
                    item["glyph_name"] = glyph.name;
                }
                break;
            }
        }
        boards_payload.push_back(std::move(item));
    }

    json attachments_payload = json::array();
    for (const Attachment& attachment : graph.attachments) {
        auto from_it = graph.node_index.find(attachment.from);
        auto to_it = graph.node_index.find(attachment.to);
        if (from_it != graph.node_index.end() && to_it != graph.node_index.end() &&
            selected_set.count(from_it->second) && selected_set.count(to_it->second)) {
            attachments_payload.push_back({{"from", attachment.from}, {"to", attachment.to}});
        }
    }

    json selected_nodes = json::array();
    for (int index : selected_indices) {
        selected_nodes.push_back(graph.nodes[index].id);
    }
    json totals_payload = map_to_json_object(totals);
    json bonus_totals_payload = map_to_json_object(bonus_totals);
    json effective_totals_payload = map_to_json_object(effective_totals);

    json result;
    result["score"] = round4(score);
    result["base_score"] = round4(base_score);
    result["type_bonus"] = round4(type_bonus);
    result["gated_node_bonus_score"] = round4(gated_node_bonus_score);
    result["node_bonus"] = round4(node_bonus);
    result["glyph_score"] = round4(glyph_score);
    result["penalties"] = round4(penalties);
    result["points_used"] = points_used;
    result["totals"] = totals_payload;
    result["bonus_totals"] = bonus_totals_payload;
    result["effective_totals"] = effective_totals_payload;
    result["glyphs"] = glyph_payload;
    result["activated_bonuses"] = activated;
    result["node_requirements"] = requirement_payload;
    result["warnings"] = warnings;
    result["boards"] = boards_payload;
    result["attachments"] = attachments_payload;
    result["selected_nodes"] = selected_nodes;
    result["selected_node_count"] = selected_indices.size();
    result["route_step_count"] = route.steps.size();
    if (include_route_steps) {
        json steps = json::array();
        for (const RouteStep& step : route.steps) {
            json item;
            item["target"] = graph.nodes[step.target].id;
            item["target_type"] = graph.nodes[step.target].type;
            item["path"] = json::array();
            for (int index : step.path) item["path"].push_back(graph.nodes[index].id);
            item["added_nodes"] = json::array();
            for (int index : step.added_nodes) item["added_nodes"].push_back(graph.nodes[index].id);
            item["cost"] = step.cost;
            item["gain_estimate"] = round4(step.gain);
            item["points_used"] = step.points_used;
            steps.push_back(std::move(item));
        }
        result["route_steps"] = std::move(steps);
    }

    ScoredRoute scored;
    scored.payload = std::move(result);
    scored.route = route;
    scored.score = round4(score);
    scored.points_used = points_used;
    scored.selected_node_count = static_cast<int>(selected_indices.size());
    return scored;
}

bool better_scored_route(const ScoredRoute& candidate, const ScoredRoute& best) {
    if (best.payload.is_null()) return true;
    if (candidate.score != best.score) return candidate.score > best.score;
    if (candidate.points_used != best.points_used) return candidate.points_used < best.points_used;
    return candidate.selected_node_count < best.selected_node_count;
}

bool route_connected_without_node(const Graph& graph, const std::vector<unsigned char>& selected, int removed_index) {
    if (removed_index == graph.start_index || !selected[graph.start_index]) {
        return false;
    }
    int expected = 0;
    for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
        if (selected[index] && index != removed_index) {
            ++expected;
        }
    }
    if (expected <= 0) {
        return false;
    }

    std::vector<unsigned char> visited(selected.size(), 0);
    std::queue<int> queue;
    queue.push(graph.start_index);
    visited[graph.start_index] = 1;
    int reached = 0;
    while (!queue.empty()) {
        int current = queue.front();
        queue.pop();
        ++reached;
        for (int neighbor : graph.adjacency[current]) {
            if (neighbor == removed_index || visited[neighbor] || !selected[neighbor]) {
                continue;
            }
            visited[neighbor] = 1;
            queue.push(neighbor);
        }
    }
    return reached == expected;
}

bool has_selected_neighbor_after_remove(
    const Graph& graph,
    const std::vector<unsigned char>& selected,
    int add_index,
    int removed_index
) {
    for (int neighbor : graph.adjacency[add_index]) {
        if (neighbor != removed_index && selected[neighbor]) {
            return true;
        }
    }
    return false;
}

std::vector<int> removable_route_nodes(const Graph& graph, const std::vector<unsigned char>& selected, const WeightModel& weights) {
    std::vector<int> candidates;
    for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
        if (!selected[index] || index == graph.start_index || graph.nodes[index].cost <= 0) {
            continue;
        }
        if (route_connected_without_node(graph, selected, index)) {
            candidates.push_back(index);
        }
    }
    auto type_rank = [](const std::string& type) {
        if (type == "normal") return 0;
        if (type == "magic") return 1;
        if (type == "board_gate") return 2;
        if (type == "rare") return 3;
        if (type == "glyph_socket") return 4;
        if (type == "legendary") return 5;
        return 6;
    };
    std::sort(candidates.begin(), candidates.end(), [&](int left, int right) {
        const GraphNode& left_node = graph.nodes[left];
        const GraphNode& right_node = graph.nodes[right];
        int left_rank = type_rank(left_node.type);
        int right_rank = type_rank(right_node.type);
        if (left_rank != right_rank) return left_rank < right_rank;
        double left_score = node_base_score(left_node, weights);
        double right_score = node_base_score(right_node, weights);
        if (std::abs(left_score - right_score) > 1e-12) return left_score < right_score;
        return left_node.id < right_node.id;
    });
    if (static_cast<int>(candidates.size()) > LOCAL_IMPROVEMENT_REMOVE_CANDIDATES) {
        candidates.resize(LOCAL_IMPROVEMENT_REMOVE_CANDIDATES);
    }
    return candidates;
}

std::vector<int> adjacent_add_nodes(
    const Graph& graph,
    const std::vector<unsigned char>& selected,
    const WeightModel& weights,
    const std::unordered_map<int, double>& route_bonuses,
    int max_cost
) {
    std::set<int> unique;
    for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
        if (!selected[index]) {
            continue;
        }
        for (int neighbor : graph.adjacency[index]) {
            if (!selected[neighbor] && graph.nodes[neighbor].cost > 0 && graph.nodes[neighbor].cost <= max_cost) {
                unique.insert(neighbor);
            }
        }
    }
    std::vector<int> candidates(unique.begin(), unique.end());
    std::sort(candidates.begin(), candidates.end(), [&](int left, int right) {
        double left_score = route_node_score(graph.nodes[left], weights, &route_bonuses, left);
        double right_score = route_node_score(graph.nodes[right], weights, &route_bonuses, right);
        if (std::abs(left_score - right_score) > 1e-12) return left_score > right_score;
        return graph.nodes[left].id < graph.nodes[right].id;
    });
    if (static_cast<int>(candidates.size()) > LOCAL_IMPROVEMENT_ADD_CANDIDATES) {
        candidates.resize(LOCAL_IMPROVEMENT_ADD_CANDIDATES);
    }
    return candidates;
}

RouteOutput improve_route_locally(
    const Graph& graph,
    const std::vector<Glyph>& glyphs,
    const WeightModel& weights,
    const std::map<std::string, double>& starting_stats,
    const ScoringContext& context,
    const std::unordered_map<int, double>& route_bonuses,
    const RouteOutput& initial_route,
    int points_limit,
    std::vector<LocalSwap>& swap_log
) {
    RouteOutput route = initial_route;
    swap_log.clear();
    double current_score = route_score_value(graph, glyphs, weights, starting_stats, context, route.selected, points_limit);
    int current_points = route_points_used(graph, route.selected);

    for (int pass = 0; pass < LOCAL_IMPROVEMENT_MAX_PASSES; ++pass) {
        std::vector<int> remove_candidates = removable_route_nodes(graph, route.selected, weights);
        if (remove_candidates.empty()) {
            break;
        }
        int max_add_cost = 0;
        for (int remove_index : remove_candidates) {
            max_add_cost = std::max(max_add_cost, graph.nodes[remove_index].cost + points_limit - current_points);
        }
        std::vector<int> add_candidates = adjacent_add_nodes(graph, route.selected, weights, route_bonuses, max_add_cost);
        if (add_candidates.empty()) {
            break;
        }

        int best_remove = -1;
        int best_add = -1;
        double best_score = current_score;
        for (int remove_index : remove_candidates) {
            int remove_cost = graph.nodes[remove_index].cost;
            if (current_points - remove_cost > points_limit) {
                continue;
            }
            for (int add_index : add_candidates) {
                int add_cost = graph.nodes[add_index].cost;
                if (current_points - remove_cost + add_cost > points_limit) {
                    continue;
                }
                if (!has_selected_neighbor_after_remove(graph, route.selected, add_index, remove_index)) {
                    continue;
                }
                double quick_delta =
                    route_node_score(graph.nodes[add_index], weights, &route_bonuses, add_index) -
                    node_base_score(graph.nodes[remove_index], weights);
                if (quick_delta <= 1e-9) {
                    continue;
                }
                std::vector<unsigned char> trial = route.selected;
                trial[remove_index] = 0;
                trial[add_index] = 1;
                double trial_score = route_score_value(graph, glyphs, weights, starting_stats, context, trial, points_limit);
                if (trial_score > best_score + 1e-9) {
                    best_score = trial_score;
                    best_remove = remove_index;
                    best_add = add_index;
                }
            }
        }

        if (best_remove < 0 || best_add < 0) {
            break;
        }
        swap_log.push_back({best_remove, best_add, current_score, best_score});
        route.selected[best_remove] = 0;
        route.selected[best_add] = 1;
        current_points = current_points - graph.nodes[best_remove].cost + graph.nodes[best_add].cost;
        current_score = best_score;
    }

    if (!swap_log.empty()) {
        route.steps.clear();
    }
    return route;
}

json local_swap_report_json(const Graph& graph, const std::vector<LocalSwap>& swap_log) {
    json report = json::array();
    for (const LocalSwap& swap : swap_log) {
        if (swap.removed < 0 || swap.added < 0 ||
            swap.removed >= static_cast<int>(graph.nodes.size()) ||
            swap.added >= static_cast<int>(graph.nodes.size())) {
            continue;
        }
        const GraphNode& removed = graph.nodes[swap.removed];
        const GraphNode& added = graph.nodes[swap.added];
        json item;
        item["removed"] = removed.id;
        item["removed_name"] = removed.name;
        item["removed_type"] = removed.type;
        item["removed_board"] = removed.board_id;
        item["removed_stats"] = map_to_json_object(removed.stats);
        item["added"] = added.id;
        item["added_name"] = added.name;
        item["added_type"] = added.type;
        item["added_board"] = added.board_id;
        item["added_stats"] = map_to_json_object(added.stats);
        item["score_before"] = round4(swap.score_before);
        item["score_after"] = round4(swap.score_after);
        item["score_delta"] = round4(swap.score_after - swap.score_before);
        report.push_back(std::move(item));
    }
    return report;
}

std::string format_value(double value) {
    std::ostringstream out;
    out << std::defaultfloat << value;
    return out.str();
}

std::string html_escape(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size());
    for (char ch : value) {
        switch (ch) {
            case '&': escaped += "&amp;"; break;
            case '<': escaped += "&lt;"; break;
            case '>': escaped += "&gt;"; break;
            case '"': escaped += "&quot;"; break;
            case '\'': escaped += "&#39;"; break;
            default: escaped += ch; break;
        }
    }
    return escaped;
}

std::string get_json_string(const json& object, const std::string& key, const std::string& fallback = "") {
    if (object.is_object()) {
        auto it = object.find(key);
        if (it != object.end() && it->is_string()) {
            return it->get<std::string>();
        }
    }
    return fallback;
}

int get_json_int(const json& object, const std::string& key, int fallback = 0) {
    if (object.is_object()) {
        auto it = object.find(key);
        if (it != object.end()) {
            if (it->is_number_integer()) return it->get<int>();
            if (it->is_number()) return static_cast<int>(it->get<double>());
        }
    }
    return fallback;
}

std::vector<std::string> get_json_string_array(const json& object, const std::string& key) {
    if (!object.is_object()) {
        return {};
    }
    auto it = object.find(key);
    if (it == object.end() || !it->is_array()) {
        return {};
    }
    return read_string_array(*it);
}

std::pair<int, int> get_outward_vector(int x, int y, int width, int height) {
    if (x == 0) return {-1, 0};
    if (y == 0) return {0, -1};
    if (x == width - 1) return {1, 0};
    if (y == height - 1) return {0, 1};
    return {0, 0};
}

const Node* find_board_node(const Board& board, const std::string& node_id) {
    auto it = board.node_by_id.find(node_id);
    if (it == board.node_by_id.end()) {
        return nullptr;
    }
    return &board.nodes[it->second];
}

std::unordered_map<std::string, std::string> build_node_to_board(const std::map<std::string, Board>& boards) {
    std::unordered_map<std::string, std::string> node_to_board;
    for (const auto& [board_id, board] : boards) {
        for (const Node& node : board.nodes) {
            node_to_board[node.id] = board_id;
        }
    }
    return node_to_board;
}

std::map<std::string, std::pair<int, int>> compute_board_offsets(
    const json& payload,
    const std::map<std::string, Board>& boards
) {
    std::map<std::string, std::pair<int, int>> offsets;
    const json& results = payload.value("results", json::array());
    if (results.empty()) {
        return offsets;
    }
    const json& best = results[0];
    const json& boards_info = best.value("boards", json::array());
    if (boards_info.empty()) {
        return offsets;
    }
    std::vector<std::string> board_order;
    std::map<std::string, int> rotations;
    for (const auto& board_info : boards_info) {
        std::string board_id = get_json_string(board_info, "id");
        if (board_id.empty()) {
            continue;
        }
        board_order.push_back(board_id);
        rotations[board_id] = get_json_int(board_info, "rotation", 0);
    }
    if (board_order.empty()) {
        return offsets;
    }

    std::unordered_set<std::string> selected_nodes;
    for (const std::string& node_id : get_json_string_array(best, "selected_nodes")) {
        selected_nodes.insert(node_id);
    }
    auto node_to_board = build_node_to_board(boards);
    offsets[board_order.front()] = {0, 0};

    const json& attachments = best.value("attachments", json::array());
    for (size_t index = 0; index + 1 < board_order.size(); ++index) {
        const std::string& previous_board = board_order[index];
        const std::string& next_board = board_order[index + 1];
        bool found = false;
        std::pair<int, int> next_offset = {offsets[previous_board].first, offsets[previous_board].second + 30};

        for (const auto& attachment : attachments) {
            std::string u = get_json_string(attachment, "from");
            std::string v = get_json_string(attachment, "to");
            auto bu_it = node_to_board.find(u);
            auto bv_it = node_to_board.find(v);
            if (bu_it == node_to_board.end() || bv_it == node_to_board.end()) {
                continue;
            }
            std::string b_u = bu_it->second;
            std::string b_v = bv_it->second;
            bool direct = b_u == previous_board && b_v == next_board;
            bool reverse = b_v == previous_board && b_u == next_board;
            if ((!direct && !reverse) || !selected_nodes.count(u) || !selected_nodes.count(v)) {
                continue;
            }
            if (reverse) {
                std::swap(u, v);
                std::swap(b_u, b_v);
            }

            const Board& left_board = boards.at(b_u);
            const Board& right_board = boards.at(b_v);
            const Node* u_node = find_board_node(left_board, u);
            const Node* v_node = find_board_node(right_board, v);
            if (!u_node || !v_node) {
                continue;
            }

            auto u_out = get_outward_vector(u_node->x, u_node->y, left_board.width, left_board.height);
            auto v_out = get_outward_vector(v_node->x, v_node->y, right_board.width, right_board.height);
            int left_rotation = rotations.count(b_u) ? rotations[b_u] : 0;
            int right_rotation = rotations.count(b_v) ? rotations[b_v] : 0;
            auto [u_rx, u_ry] = rotate_point(u_node->x, u_node->y, left_board.width, left_board.height, left_rotation);
            auto [ux_plus, uy_plus] = rotate_point(
                u_node->x + u_out.first,
                u_node->y + u_out.second,
                left_board.width,
                left_board.height,
                left_rotation
            );
            auto [v_rx, v_ry] = rotate_point(v_node->x, v_node->y, right_board.width, right_board.height, right_rotation);
            auto [vx_plus, vy_plus] = rotate_point(
                v_node->x + v_out.first,
                v_node->y + v_out.second,
                right_board.width,
                right_board.height,
                right_rotation
            );
            std::pair<int, int> u_dir = {ux_plus - u_rx, uy_plus - u_ry};
            std::pair<int, int> v_dir = {vx_plus - v_rx, vy_plus - v_ry};
            if (u_dir.first == -v_dir.first && u_dir.second == -v_dir.second) {
                next_offset = {
                    offsets[previous_board].first + u_rx + u_dir.first - v_rx,
                    offsets[previous_board].second + u_ry + u_dir.second - v_ry
                };
                found = true;
                break;
            }
        }

        offsets[next_board] = next_offset;
        (void)found;
    }

    return offsets;
}

std::string generate_html_visual(const json& payload, const std::map<std::string, Board>& boards) {
    const json& results = payload.value("results", json::array());
    if (results.empty()) {
        return "<html><body><h1>No route found</h1></body></html>";
    }
    const json& best = results[0];
    const json& boards_info = best.value("boards", json::array());
    std::map<std::string, json> board_info_by_id;
    std::vector<std::string> board_order;
    std::map<std::string, int> rotations;
    for (const auto& board_info : boards_info) {
        std::string board_id = get_json_string(board_info, "id");
        if (board_id.empty()) continue;
        board_order.push_back(board_id);
        board_info_by_id[board_id] = board_info;
        rotations[board_id] = get_json_int(board_info, "rotation", 0);
    }

    std::unordered_set<std::string> selected_nodes;
    for (const std::string& node_id : get_json_string_array(best, "selected_nodes")) {
        selected_nodes.insert(node_id);
    }

    std::map<std::string, std::string> board_glyphs;
    std::map<std::string, std::string> glyph_names;
    for (const auto& board_info : boards_info) {
        std::string board_id = get_json_string(board_info, "id");
        std::string glyph = get_json_string(board_info, "glyph");
        if (!board_id.empty() && !glyph.empty()) {
            board_glyphs[board_id] = glyph;
        }
    }
    for (const auto& glyph_info : best.value("glyphs", json::array())) {
        std::string glyph = get_json_string(glyph_info, "glyph");
        if (!glyph.empty()) {
            glyph_names[glyph] = localized_name(glyph_info.value("name", json::object()), glyph);
        }
    }

    std::map<std::string, int> glyph_legend;
    int glyph_counter = 1;
    for (const auto& board_info : boards_info) {
        std::string board_id = get_json_string(board_info, "id");
        auto glyph_it = board_glyphs.find(board_id);
        if (glyph_it != board_glyphs.end() && !glyph_legend.count(glyph_it->second)) {
            glyph_legend[glyph_it->second] = glyph_counter++;
        }
    }

    constexpr double NODE_SIZE = 24.0;
    constexpr double NODE_SPACING = 30.0;

    struct VisualNode {
        int gx = 0;
        int gy = 0;
        std::string type;
        std::string board_id;
        bool selected = false;
    };
    std::map<std::string, VisualNode> global_nodes;
    int min_x = std::numeric_limits<int>::max();
    int max_x = std::numeric_limits<int>::min();
    int min_y = std::numeric_limits<int>::max();
    int max_y = std::numeric_limits<int>::min();
    auto offsets = compute_board_offsets(payload, boards);

    for (const std::string& board_id : board_order) {
        auto board_it = boards.find(board_id);
        if (board_it == boards.end() || !offsets.count(board_id)) {
            continue;
        }
        const Board& board = board_it->second;
        auto [ox, oy] = offsets[board_id];
        int rotation = rotations.count(board_id) ? rotations[board_id] : 0;
        for (const Node& node : board.nodes) {
            auto [rx, ry] = rotate_point(node.x, node.y, board.width, board.height, rotation);
            int gx = ox + rx;
            int gy = oy + ry;
            global_nodes[node.id] = {gx, gy, node.type, board_id, selected_nodes.count(node.id) > 0};
            min_x = std::min(min_x, gx);
            max_x = std::max(max_x, gx);
            min_y = std::min(min_y, gy);
            max_y = std::max(max_y, gy);
        }
    }

    if (global_nodes.empty()) {
        return "<html><body><h1>No route found</h1></body></html>";
    }

    int svg_width = static_cast<int>((max_x - min_x + 2) * NODE_SPACING);
    int svg_height = static_cast<int>((max_y - min_y + 3) * NODE_SPACING);
    auto to_svg_coord = [&](double gx, double gy) {
        return std::pair<double, double>{(gx - min_x + 1) * NODE_SPACING, (gy - min_y + 2) * NODE_SPACING};
    };

    std::ostringstream html;
    html << "<!DOCTYPE html>\n<html lang='ru'>\n<head>\n<meta charset='UTF-8'>\n";
    html << "<title>Маршрут парагона</title>\n<style>\n";
    html << "body { font-family: sans-serif; background: #1a1a1a; color: #ddd; margin: 20px; }\n";
    html << ".legend { margin-top: 30px; padding: 15px; background: #2a2a2a; border-radius: 8px; border: 1px solid #444; max-width: 400px; }\n";
    html << ".requirements { max-width: 900px; }\n";
    html << ".req-ok { color: #9be28f; }\n.req-miss { color: #ff8f8f; }\n";
    html << ".legend-title { font-weight: bold; margin-bottom: 10px; color: #fff; }\n";
    html << ".svg-container { overflow-x: auto; background: #222; border: 1px solid #444; border-radius: 8px; padding: 20px; margin-top: 20px; }\n";
    html << "</style>\n</head>\n<body>\n";
    html << "<h1>Маршрут парагона - " << html_escape(payload.value("class", "Unknown")) << "</h1>\n";
    html << "<p>Счет: " << format_value(best.value("score", 0.0)) << " | Очки: "
         << best.value("points_used", 0) << "/" << payload.value("points_limit", 0) << "</p>\n";
    html << "<div class='svg-container'>\n";
    html << "<svg width='" << svg_width << "' height='" << svg_height << "'>\n";

    for (const auto& glyph_info : best.value("glyphs", json::array())) {
        std::string socket_id = get_json_string(glyph_info, "socket");
        auto node_it = global_nodes.find(socket_id);
        if (node_it == global_nodes.end()) continue;
        int radius = static_cast<int>(as_double(glyph_info.value("radius", 0.0)));
        for (int dx = -radius; dx <= radius; ++dx) {
            for (int dy = -radius; dy <= radius; ++dy) {
                if (std::abs(dx) + std::abs(dy) <= radius) {
                    auto [cx, cy] = to_svg_coord(node_it->second.gx + dx, node_it->second.gy + dy);
                    html << "<rect x='" << (cx - NODE_SPACING / 2.0) << "' y='" << (cy - NODE_SPACING / 2.0)
                         << "' width='" << NODE_SPACING << "' height='" << NODE_SPACING
                         << "' fill='#ff9900' opacity='0.15' />\n";
                }
            }
        }
    }

    std::set<std::string> edges_drawn;
    auto edge_key = [](const std::string& left, const std::string& right) {
        return left < right ? left + "\n" + right : right + "\n" + left;
    };

    for (const std::string& board_id : board_order) {
        auto board_it = boards.find(board_id);
        if (board_it == boards.end()) continue;
        for (const auto& [u, v] : board_it->second.edges) {
            auto u_it = global_nodes.find(u);
            auto v_it = global_nodes.find(v);
            if (u_it == global_nodes.end() || v_it == global_nodes.end()) continue;
            std::string key = edge_key(u, v);
            if (!edges_drawn.insert(key).second) continue;
            auto [ux, uy] = to_svg_coord(u_it->second.gx, u_it->second.gy);
            auto [vx, vy] = to_svg_coord(v_it->second.gx, v_it->second.gy);
            bool selected = u_it->second.selected && v_it->second.selected;
            html << "<line x1='" << ux << "' y1='" << uy << "' x2='" << vx << "' y2='" << vy
                 << "' stroke='" << (selected ? "#ff4444" : "#444444")
                 << "' stroke-width='" << (selected ? 3 : 1) << "' />\n";
        }
    }

    for (const auto& attachment : best.value("attachments", json::array())) {
        std::string u = get_json_string(attachment, "from");
        std::string v = get_json_string(attachment, "to");
        auto u_it = global_nodes.find(u);
        auto v_it = global_nodes.find(v);
        if (u_it == global_nodes.end() || v_it == global_nodes.end()) continue;
        int dx = u_it->second.gx - v_it->second.gx;
        int dy = u_it->second.gy - v_it->second.gy;
        if (dx * dx + dy * dy > 2) continue;
        std::string key = edge_key(u, v);
        if (!edges_drawn.insert(key).second) continue;
        auto [ux, uy] = to_svg_coord(u_it->second.gx, u_it->second.gy);
        auto [vx, vy] = to_svg_coord(v_it->second.gx, v_it->second.gy);
        bool selected = u_it->second.selected && v_it->second.selected;
        html << "<line x1='" << ux << "' y1='" << uy << "' x2='" << vx << "' y2='" << vy
             << "' stroke='" << (selected ? "#ff4444" : "#6666ff")
             << "' stroke-width='" << (selected ? 3 : 2) << "' "
             << (selected ? "" : "stroke-dasharray='4'") << " />\n";
    }

    struct Bounds {
        int min_x = std::numeric_limits<int>::max();
        int max_x = std::numeric_limits<int>::min();
        int min_y = std::numeric_limits<int>::max();
        int max_y = std::numeric_limits<int>::min();
    };
    std::map<std::string, Bounds> board_bounds;
    for (const auto& [_, node] : global_nodes) {
        Bounds& bounds = board_bounds[node.board_id];
        bounds.min_x = std::min(bounds.min_x, node.gx);
        bounds.max_x = std::max(bounds.max_x, node.gx);
        bounds.min_y = std::min(bounds.min_y, node.gy);
        bounds.max_y = std::max(bounds.max_y, node.gy);
    }
    for (const auto& [board_id, bounds] : board_bounds) {
        double center_gx = (bounds.min_x + bounds.max_x) / 2.0;
        auto [cx, cy] = to_svg_coord(center_gx, bounds.min_y);
        json board_name_json = board_info_by_id.count(board_id) ? board_info_by_id[board_id].value("name", json::object()) : boards.at(board_id).name;
        std::string board_name = localized_name(board_name_json, board_id);
        int turns = board_info_by_id.count(board_id) ? get_json_int(board_info_by_id[board_id], "rotation_turns", 0) : 0;
        int points_remaining = board_info_by_id.count(board_id)
            ? get_json_int(board_info_by_id[board_id], "points_remaining_before_board", -1)
            : -1;
        std::string title = board_name + " (поворот " + std::to_string(turns);
        if (points_remaining >= 0) {
            title += ", вход: " + std::to_string(points_remaining) + " очк.";
        }
        title += ")";
        html << "<text x='" << cx << "' y='" << (cy - 20)
             << "' font-size='18' font-weight='bold' font-family='sans-serif' fill='#aaa' text-anchor='middle'>"
             << html_escape(title) << "</text>\n";
    }

    for (const auto& [node_id, node] : global_nodes) {
        auto [cx, cy] = to_svg_coord(node.gx, node.gy);
        std::string fill_color = "#555";
        std::string stroke_color = "#333";
        if (node.type == "normal") fill_color = "#888";
        else if (node.type == "magic") fill_color = "#4a90e2";
        else if (node.type == "rare") fill_color = "#f5a623";
        else if (node.type == "legendary") fill_color = "#d0021b";
        else if (node.type == "board_gate") fill_color = "#9b59b6";
        else if (node.type == "glyph_socket") fill_color = "#fff";
        double opacity = node.selected ? 1.0 : 0.3;
        double radius = NODE_SIZE / 2.0;
        if (node.type == "glyph_socket") {
            radius = NODE_SIZE * 0.8;
            stroke_color = "#f5a623";
        }
        int stroke_width = node.selected ? 2 : 1;
        if (node.selected) stroke_color = "#fff";
        html << "<circle cx='" << cx << "' cy='" << cy << "' r='" << radius << "' fill='" << fill_color
             << "' stroke='" << stroke_color << "' stroke-width='" << stroke_width << "' opacity='" << opacity
             << "'><title>" << html_escape(node_id + " (" + node.type + ")") << "</title></circle>\n";
        if (node.type == "glyph_socket") {
            auto glyph_it = board_glyphs.find(node.board_id);
            if (glyph_it != board_glyphs.end() && glyph_legend.count(glyph_it->second)) {
                html << "<text x='" << cx << "' y='" << cy
                     << "' font-size='14' font-weight='bold' font-family='sans-serif' fill='#000' text-anchor='middle' dominant-baseline='central' opacity='"
                     << opacity << "'>" << glyph_legend[glyph_it->second] << "</text>\n";
            }
        }
    }

    html << "</svg>\n</div>\n";
    html << "<div class='legend'>\n<div class='legend-title'>Вход в доски</div>\n";
    for (const auto& board_info : boards_info) {
        std::string board_id = get_json_string(board_info, "id");
        json board_name_json = board_info.value("name", json::object());
        std::string board_name = localized_name(board_name_json, board_id);
        int before = get_json_int(board_info, "points_remaining_before_board", -1);
        int after_first = get_json_int(board_info, "points_remaining_after_first_board_node", -1);
        std::string first_node = get_json_string(board_info, "first_selected_node");
        html << "<div><strong>" << html_escape(board_name) << "</strong>: ";
        if (before >= 0) {
            html << "на входе " << before << " очк.";
            if (after_first >= 0) {
                html << ", после первой клетки " << after_first << " очк.";
            }
            if (!first_node.empty()) {
                html << " (" << html_escape(first_node) << ")";
            }
        } else {
            html << "нет данных";
        }
        html << "</div>\n";
    }
    html << "</div>\n";
    const json& node_requirements = best.value("node_requirements", json::array());
    if (!node_requirements.empty()) {
        auto requirement_text = [](const json& item) {
            std::ostringstream text;
            const json& requirements = item.value("requirements", json::object());
            const json& effective = item.value("effective_totals", json::object());
            bool first = true;
            for (auto it = requirements.begin(); it != requirements.end(); ++it) {
                if (!first) text << ", ";
                first = false;
                text << it.key() << " " << format_value(as_double(effective.value(it.key(), 0.0)))
                     << "/" << format_value(as_double(it.value()));
            }
            return text.str();
        };
        html << "<div class='legend requirements'>\n<div class='legend-title'>Требования редких нод</div>\n";
        for (const auto& item : node_requirements) {
            std::string node_id = get_json_string(item, "node");
            std::string board_id = get_json_string(item, "board");
            std::string name = localized_name(item.value("name", json::object()), node_id);
            bool met = item.value("met", false);
            html << "<div class='" << (met ? "req-ok" : "req-miss") << "'><strong>"
                 << html_escape(name) << "</strong> (" << html_escape(board_id) << "): "
                 << html_escape(requirement_text(item)) << "</div>\n";
        }
        html << "</div>\n";
    }
    const json& local_swap_report = best.value("local_swap_report", json::array());
    if (!local_swap_report.empty()) {
        auto stats_text = [](const json& stats) {
            std::ostringstream text;
            if (!stats.is_object()) {
                return std::string("");
            }
            bool first = true;
            for (auto it = stats.begin(); it != stats.end(); ++it) {
                if (!first) text << ", ";
                first = false;
                text << it.key() << "=" << format_value(as_double(it.value()));
            }
            return text.str();
        };
        html << "<div class='legend requirements'>\n<div class='legend-title'>Локальные замены</div>\n";
        for (const auto& item : local_swap_report) {
            std::string removed_id = get_json_string(item, "removed");
            std::string added_id = get_json_string(item, "added");
            std::string removed_name = localized_name(item.value("removed_name", json::object()), removed_id);
            std::string added_name = localized_name(item.value("added_name", json::object()), added_id);
            html << "<div><strong>" << html_escape(removed_name) << "</strong> ["
                 << html_escape(stats_text(item.value("removed_stats", json::object()))) << "] -> <strong>"
                 << html_escape(added_name) << "</strong> ["
                 << html_escape(stats_text(item.value("added_stats", json::object()))) << "], +"
                 << html_escape(format_value(as_double(item.value("score_delta", 0.0)))) << "</div>\n";
        }
        html << "</div>\n";
    }
    html << "<div class='legend'>\n<div class='legend-title'>Глифы</div>\n";
    if (!glyph_legend.empty()) {
        std::vector<std::pair<std::string, int>> items(glyph_legend.begin(), glyph_legend.end());
        std::sort(items.begin(), items.end(), [](const auto& left, const auto& right) {
            return left.second < right.second;
        });
        for (const auto& [glyph, index] : items) {
            std::string display_name = glyph_names.count(glyph) ? glyph_names[glyph] : glyph;
            html << "<div><strong>[" << index << "]</strong> — " << html_escape(display_name) << "</div>\n";
        }
    } else {
        html << "<div>Глифы не вставлены.</div>\n";
    }
    html << "</div>\n</body>\n</html>";
    return html.str();
}

std::string timestamp_for_filename() {
    auto now = std::chrono::system_clock::now();
    std::time_t now_time = std::chrono::system_clock::to_time_t(now);
    std::tm local_time{};
#ifdef _WIN32
    localtime_s(&local_time, &now_time);
#else
    localtime_r(&now_time, &local_time);
#endif
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%d_%m_%Y_%H_%M_%S", &local_time);
    return buffer;
}

std::string path_for_json(const fs::path& path) {
    return path.generic_string();
}

std::string result_summary(const json& payload) {
    const auto& results = payload.value("results", json::array());
    if (results.empty()) {
        return "No route found.";
    }
    const json& best = results[0];
    std::vector<std::string> lines;
    lines.push_back(
        "Best score " + format_value(best.value("score", 0.0)) +
        " with " + std::to_string(best.value("points_used", 0)) + "/" +
        std::to_string(payload.value("points_limit", 0)) + " points for " +
        payload.value("class", "") + "."
    );
    std::vector<std::string> board_bits;
    for (const auto& board : best.value("boards", json::array())) {
        std::string name = localized_name(board.value("name", json::object()), board.value("id", ""));
        int turns = board.value("rotation_turns", 0);
        std::string bit = name + " (поворот " + std::to_string(turns) + ")";
        if (board.contains("glyph") && board["glyph"].is_string()) {
            std::string glyph_name = localized_name(board.value("glyph_name", json::object()), board["glyph"].get<std::string>());
            bit += ", глиф=" + glyph_name;
        }
        board_bits.push_back(bit);
    }
    std::ostringstream board_line;
    board_line << "Boards: ";
    for (size_t i = 0; i < board_bits.size(); ++i) {
        if (i) board_line << " -> ";
        board_line << board_bits[i];
    }
    lines.push_back(board_line.str());
    std::vector<std::pair<std::string, double>> totals;
    if (best.contains("totals") && best["totals"].is_object()) {
        for (auto it = best["totals"].begin(); it != best["totals"].end(); ++it) {
            totals.push_back({it.key(), as_double(it.value())});
        }
    }
    std::sort(totals.begin(), totals.end(), [](const auto& left, const auto& right) {
        if (std::abs(std::abs(left.second) - std::abs(right.second)) > 1e-12) {
            return std::abs(left.second) > std::abs(right.second);
        }
        return left.first < right.first;
    });
    if (!totals.empty()) {
        std::ostringstream line;
        line << "Top stats: ";
        for (size_t i = 0; i < std::min<size_t>(8, totals.size()); ++i) {
            if (i) line << ", ";
            line << totals[i].first << "=" << format_value(totals[i].second);
        }
        lines.push_back(line.str());
    }
    std::vector<std::string> activated = best.value("activated_bonuses", std::vector<std::string>{});
    if (!activated.empty()) {
        std::ostringstream line;
        line << "Activated nodes: ";
        for (size_t i = 0; i < std::min<size_t>(12, activated.size()); ++i) {
            if (i) line << ", ";
            line << activated[i];
        }
        lines.push_back(line.str());
    }
    const json& node_requirements = best.value("node_requirements", json::array());
    if (!node_requirements.empty()) {
        int met_count = 0;
        for (const auto& item : node_requirements) {
            if (item.value("met", false)) ++met_count;
        }
        lines.push_back(
            "Node requirements: " + std::to_string(met_count) + "/" +
            std::to_string(node_requirements.size()) + " selected rare/legendary bonuses active."
        );
    }
    int local_swaps = best.value("local_swaps", 0);
    if (local_swaps > 0) {
        lines.push_back("Local swaps: " + std::to_string(local_swaps) + " post-route replacements accepted.");
    }
    std::vector<std::string> warnings = payload.value("warnings", std::vector<std::string>{});
    std::vector<std::string> best_warnings = best.value("warnings", std::vector<std::string>{});
    warnings.insert(warnings.end(), best_warnings.begin(), best_warnings.end());
    if (!warnings.empty()) {
        std::ostringstream line;
        line << "Warnings: ";
        std::set<std::string> seen;
        bool first = true;
        for (const std::string& warning : warnings) {
            if (!seen.insert(warning).second) continue;
            if (!first) line << "; ";
            first = false;
            line << warning;
        }
        lines.push_back(line.str());
    }
    const json& search = payload.value("search", json::object());
    lines.push_back(
        "Search: " + std::to_string(search.value("variants_checked", 0)) +
        " variants, " + std::to_string(search.value("routes_checked", 0)) +
        " routes, " + format_value(search.value("elapsed_seconds", 0.0)) + "s."
    );
    std::ostringstream result;
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i) result << "\n";
        result << lines[i];
    }
    return result.str();
}

int resolve_workers(int requested) {
    if (requested > 0) {
        return requested;
    }
    unsigned int cores = std::thread::hardware_concurrency();
    if (cores == 0) cores = 1;
    return std::max(1u, cores / 2);
}

bool parse_bool_value(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return std::tolower(c); });
    return value == "1" || value == "true" || value == "yes" || value == "y" || value == "да";
}

bool read_bool_field(const json& value, const std::string& field_name) {
    if (value.is_boolean()) {
        return value.get<bool>();
    }
    if (value.is_string()) {
        return parse_bool_value(value.get<std::string>());
    }
    if (value.is_number()) {
        return as_double(value) != 0.0;
    }
    throw std::runtime_error("profile field must be boolean-compatible: " + field_name);
}

fs::path profile_relative_path(const fs::path& profile_path, const std::string& raw_path) {
    fs::path path(raw_path);
    if (path.is_absolute()) {
        return path;
    }
    fs::path base = fs::absolute(profile_path).parent_path();
    return (base / path).lexically_normal();
}

void apply_profile(Options& options) {
    if (options.profile_path.empty()) {
        return;
    }
    json raw = read_json(options.profile_path);
    if (!raw.is_object()) {
        throw std::runtime_error("profile must be a JSON object: " + options.profile_path.string());
    }
    if (raw.contains("class")) {
        options.class_slug = raw["class"].get<std::string>();
    }
    if (raw.contains("points")) {
        options.points = static_cast<int>(as_double(raw["points"]));
    }
    if (raw.contains("legendary_glyphs")) {
        options.legendary_glyphs = read_bool_field(raw["legendary_glyphs"], "legendary_glyphs");
    }
    if (raw.contains("weights")) {
        options.weights_path = profile_relative_path(options.profile_path, raw["weights"].get<std::string>());
    }
    if (raw.contains("starting_stats")) {
        options.starting_stats = read_float_map(raw["starting_stats"]);
    }
    if (raw.contains("max_routes")) {
        options.max_routes = static_cast<int>(as_double(raw["max_routes"]));
    }
    if (raw.contains("candidate_targets")) {
        options.candidate_targets = static_cast<int>(as_double(raw["candidate_targets"]));
    }
    if (raw.contains("workers")) {
        options.workers = static_cast<int>(as_double(raw["workers"]));
    }
    if (raw.contains("include_route_steps")) {
        options.include_route_steps = read_bool_field(raw["include_route_steps"], "include_route_steps");
    }
    if (raw.contains("no_html")) {
        options.no_html = read_bool_field(raw["no_html"], "no_html");
    }
    if (raw.contains("scheme")) {
        options.scheme = read_string_array(raw["scheme"]);
    }
    if (raw.contains("data")) {
        options.data_root = profile_relative_path(options.profile_path, raw["data"].get<std::string>());
    }
}

Options parse_args(int argc, char** argv) {
    Options options;
    if (argc < 2) {
        throw std::runtime_error("usage: paragon_optimize <schema|optimize> --profile profile.json");
    }
    options.command = argv[1];
    fs::path exe_path = fs::absolute(argv[0]).parent_path();
    options.data_root = exe_path.parent_path() / "data" / "normalized";

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--profile") {
            if (i + 1 >= argc) {
                throw std::runtime_error("missing value for --profile");
            }
            options.profile_path = argv[++i];
        }
    }
    apply_profile(options);

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        auto require_value = [&](const std::string& name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error("missing value for " + name);
            }
            return argv[++i];
        };
        if (arg == "--profile") options.profile_path = require_value(arg);
        else if (arg == "--class") options.class_slug = require_value(arg);
        else if (arg == "--points") options.points = std::stoi(require_value(arg));
        else if (arg == "--legendary-glyphs") {
            options.legendary_glyphs = parse_bool_value(require_value(arg));
        } else if (arg == "--weights") options.weights_path = require_value(arg);
        else if (arg == "--data") options.data_root = require_value(arg);
        else if (arg == "--max-routes") options.max_routes = std::stoi(require_value(arg));
        else if (arg == "--candidate-targets") options.candidate_targets = std::stoi(require_value(arg));
        else if (arg == "--workers") options.workers = std::stoi(require_value(arg));
        else if (arg == "--include-route-steps") options.include_route_steps = true;
        else if (arg == "--no-html") options.no_html = true;
        else if (arg == "--scheme") {
            while (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) {
                options.scheme.push_back(argv[++i]);
            }
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (options.command != "schema" && options.command != "optimize") {
        throw std::runtime_error("native CLI supports only schema and optimize commands");
    }
    if (options.class_slug.empty()) throw std::runtime_error("--class is required");
    if (options.command == "optimize") {
        if (options.points <= 0) throw std::runtime_error("--points is required");
        if (options.weights_path.empty()) throw std::runtime_error("--weights is required");
    }
    return options;
}

json class_schema(const Options& options) {
    json raw = read_json(options.data_root / "classes" / (options.class_slug + ".json"));
    if (raw.value("class", "") != options.class_slug) {
        throw std::runtime_error("class reference mismatch: " + options.class_slug);
    }
    json example_weights = json::object();
    std::vector<std::string> stats = read_string_array(raw.value("available_stats", json::array()));
    for (size_t i = 0; i < std::min<size_t>(2, stats.size()); ++i) {
        example_weights[stats[i]] = 1.0;
    }
    return {
        {"class", raw.value("class", options.class_slug)},
        {"name", object_or_empty(raw.value("name", json::object()))},
        {"available_stats", raw.value("available_stats", json::array())},
        {"available_boards", raw.value("boards", json::array())},
        {"available_glyphs", raw.value("glyphs", json::array())},
        {"primary_attributes", raw.value("primary_attributes", json::array())},
        {"patch_version", raw.contains("patch_version") ? raw["patch_version"] : json(nullptr)},
        {"weight_schema_example", {
            {"weights", example_weights},
            {"glyph_route", glyph_route_tuning_json(GlyphRouteTuning{})},
            {"scheme", {{"starter", 10.0}}}
        }}
    };
}

json optimize(const Options& options) {
    auto started = std::chrono::steady_clock::now();
    int worker_count = resolve_workers(options.workers);
    ClassRef class_ref = load_class(options.data_root, options.class_slug);
    WeightModel weights = load_weights(options.weights_path);
    auto boards = load_boards(options.data_root, class_ref);
    auto glyphs = load_glyphs(options.data_root, class_ref);
    std::vector<std::string> scheme = effective_scheme(weights, class_ref, options);
    auto sequences = board_sequences(class_ref, boards, weights, scheme);

    ScoredRoute best;
    Graph best_graph;
    std::vector<std::string> best_sequence;
    ScoringContext best_scoring_context;
    std::unordered_map<int, double> best_route_bonuses;
    bool have_best_context = false;
    int variants_checked = 0;
    int routes_checked = 0;
    bool stopped_by_limit = false;
    std::vector<std::string> warnings;

    for (const auto& sequence : sequences) {
        if (stopped_by_limit) break;
        generate_layouts(boards, sequence, [&](const std::map<std::string, int>& rotations, const std::vector<Attachment>& attachments) {
            if (stopped_by_limit) return;
            if (options.max_routes > 0 && routes_checked >= options.max_routes) {
                stopped_by_limit = true;
                warnings.push_back("search stopped by route limit: " + std::to_string(options.max_routes));
                return;
            }
            Graph graph = build_combined_graph(boards, sequence, rotations, attachments);
            ScoringContext scoring_context = build_scoring_context(graph, glyphs, weights, options.legendary_glyphs);
            auto route_bonuses = glyph_route_node_bonuses(graph, glyphs, weights, scoring_context);
            variants_checked += 1;
            std::vector<int> targets = candidate_targets(graph, weights, options.candidate_targets, route_bonuses);
            std::vector<int> seeds;
            seeds.push_back(-1);
            seeds.insert(seeds.end(), targets.begin(), targets.end());
            if (options.max_routes > 0) {
                int remaining = options.max_routes - routes_checked;
                if (remaining < static_cast<int>(seeds.size())) {
                    seeds.resize(std::max(remaining, 0));
                }
            }
            if (seeds.empty()) {
                return;
            }

            RouteInput input = build_route_input(graph, weights, options.points, targets, route_bonuses);
            std::vector<ScoredRoute> scored(seeds.size());
            std::atomic<size_t> next_index(0);
            int layout_workers = std::min(worker_count, static_cast<int>(seeds.size()));
            std::vector<std::thread> threads;
            threads.reserve(layout_workers);
            std::exception_ptr error = nullptr;
            std::mutex error_mutex;
            for (int worker = 0; worker < layout_workers; ++worker) {
                threads.emplace_back([&]() {
                    try {
                        while (true) {
                            size_t index = next_index.fetch_add(1);
                            if (index >= seeds.size()) break;
                            RouteOutput route = run_greedy_route(input, ordered_targets(input, seeds[index]));
                            scored[index] = score_route(
                                graph,
                                boards,
                                sequence,
                                glyphs,
                                weights,
                                options.starting_stats,
                                scoring_context,
                                route,
                                options.points,
                                options.include_route_steps
                            );
                            scored[index].route = std::move(route);
                        }
                    } catch (...) {
                        std::lock_guard<std::mutex> lock(error_mutex);
                        if (!error) error = std::current_exception();
                    }
                });
            }
            for (std::thread& thread : threads) {
                thread.join();
            }
            if (error) {
                std::rethrow_exception(error);
            }
            for (ScoredRoute& item : scored) {
                if (better_scored_route(item, best)) {
                    best = std::move(item);
                    best.payload["class"] = class_ref.class_slug;
                    best.payload["points_limit"] = options.points;
                    best_graph = graph;
                    best_sequence = sequence;
                    best_scoring_context = scoring_context;
                    best_route_bonuses = route_bonuses;
                    have_best_context = true;
                }
                routes_checked += 1;
            }
            if (options.max_routes > 0 && routes_checked >= options.max_routes) {
                stopped_by_limit = true;
                warnings.push_back("search stopped by route limit: " + std::to_string(options.max_routes));
            }
        });
    }

    if (!best.payload.is_null() && have_best_context) {
        std::vector<LocalSwap> swap_log;
        RouteOutput improved_route = improve_route_locally(
            best_graph,
            glyphs,
            weights,
            options.starting_stats,
            best_scoring_context,
            best_route_bonuses,
            best.route,
            options.points,
            swap_log
        );
        if (!swap_log.empty()) {
            ScoredRoute improved = score_route(
                best_graph,
                boards,
                best_sequence,
                glyphs,
                weights,
                options.starting_stats,
                best_scoring_context,
                improved_route,
                options.points,
                options.include_route_steps
            );
            improved.route = std::move(improved_route);
            improved.local_swaps = best.local_swaps + static_cast<int>(swap_log.size());
            improved.payload["local_swaps"] = improved.local_swaps;
            improved.payload["local_swap_report"] = local_swap_report_json(best_graph, swap_log);
            improved.payload["local_score_before"] = best.score;
            improved.payload["class"] = class_ref.class_slug;
            improved.payload["points_limit"] = options.points;
            if (better_scored_route(improved, best)) {
                best = std::move(improved);
            }
        }
    }

    auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    json payload;
    payload["class"] = class_ref.class_slug;
    payload["points_limit"] = options.points;
    payload["legendary_glyphs"] = options.legendary_glyphs;
    if (!options.profile_path.empty()) {
        payload["profile_file"] = path_for_json(options.profile_path);
    }
    payload["weights_file"] = path_for_json(options.weights_path);
    payload["starting_stats"] = options.starting_stats;
    payload["results"] = json::array();
    if (!best.payload.is_null()) {
        payload["results"].push_back(best.payload);
    }
    payload["search"] = {
        {"variants_checked", variants_checked},
        {"routes_checked", routes_checked},
        {"workers", worker_count},
        {"stopped_by_limit", stopped_by_limit},
        {"elapsed_seconds", round4(elapsed)},
        {"limits", {
            {"max_routes", options.max_routes > 0 ? json(options.max_routes) : json(nullptr)},
            {"candidate_targets", options.candidate_targets > 0 ? json(options.candidate_targets) : json(nullptr)}
        }},
        {"glyph_route", {
            {"model", "expected_v2"},
            {"tuning", glyph_route_tuning_json(weights.glyph_route)}
        }}
    };
    payload["warnings"] = warnings;
    if (!options.no_html) {
        fs::path out_dir = fs::current_path() / "out";
        fs::create_directories(out_dir);
        fs::path html_path = out_dir / (timestamp_for_filename() + ".html");
        std::ofstream html_file(html_path, std::ios::binary);
        if (!html_file) {
            throw std::runtime_error("failed to write html file: " + html_path.string());
        }
        html_file << generate_html_visual(payload, boards);
        html_file.close();
        payload["html_file"] = path_for_json(fs::relative(html_path, fs::current_path()));
    }
    payload["summary"] = result_summary(payload);
    return payload;
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
#endif
    try {
        Options options = parse_args(argc, argv);
        json payload = options.command == "schema" ? class_schema(options) : optimize(options);
        std::cout << payload.dump(2) << "\n";
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "error: " << exc.what() << "\n";
        return 1;
    }
}
