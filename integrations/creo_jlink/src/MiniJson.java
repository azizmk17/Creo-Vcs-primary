import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class MiniJson {
    private MiniJson() {
    }

    public static Object parse(String text) {
        Parser parser = new Parser(text == null ? "" : text);
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw new IllegalArgumentException("Unexpected JSON content at position " + parser.position());
        }
        return value;
    }

    public static String stringify(Object value) {
        StringBuilder output = new StringBuilder();
        writeValue(output, value);
        return output.toString();
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("Expected a JSON object.");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> array(Object value) {
        if (value == null) {
            return new ArrayList<Object>();
        }
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("Expected a JSON array.");
        }
        return (List<Object>) value;
    }

    public static String text(Map<String, Object> value, String key) {
        Object item = value == null ? null : value.get(key);
        return item == null ? "" : String.valueOf(item);
    }

    public static boolean bool(Map<String, Object> value, String key) {
        Object item = value == null ? null : value.get(key);
        if (item instanceof Boolean) {
            return ((Boolean) item).booleanValue();
        }
        return "true".equalsIgnoreCase(String.valueOf(item));
    }

    public static int integer(Map<String, Object> value, String key) {
        Object item = value == null ? null : value.get(key);
        if (item == null) {
            return 0;
        }
        if (item instanceof Number) {
            return ((Number) item).intValue();
        }
        String text = String.valueOf(item).trim();
        if (text.length() == 0 || "null".equalsIgnoreCase(text)) {
            return 0;
        }
        return Integer.parseInt(text);
    }

    private static void writeValue(StringBuilder output, Object value) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof String || value instanceof Character) {
            writeString(output, String.valueOf(value));
        } else if (value instanceof Number || value instanceof Boolean) {
            output.append(String.valueOf(value));
        } else if (value instanceof Map) {
            output.append('{');
            boolean first = true;
            Iterator<?> entries = ((Map<?, ?>) value).entrySet().iterator();
            while (entries.hasNext()) {
                Map.Entry<?, ?> entry = (Map.Entry<?, ?>) entries.next();
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeString(output, String.valueOf(entry.getKey()));
                output.append(':');
                writeValue(output, entry.getValue());
            }
            output.append('}');
        } else if (value instanceof Iterable) {
            output.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeValue(output, item);
            }
            output.append(']');
        } else {
            writeString(output, String.valueOf(value));
        }
    }

    private static void writeString(StringBuilder output, String value) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            switch (current) {
                case '"': output.append("\\\""); break;
                case '\\': output.append("\\\\"); break;
                case '\b': output.append("\\b"); break;
                case '\f': output.append("\\f"); break;
                case '\n': output.append("\\n"); break;
                case '\r': output.append("\\r"); break;
                case '\t': output.append("\\t"); break;
                default:
                    if (current < 0x20) {
                        String hex = Integer.toHexString(current);
                        output.append("\\u");
                        for (int pad = hex.length(); pad < 4; pad++) {
                            output.append('0');
                        }
                        output.append(hex);
                    } else {
                        output.append(current);
                    }
            }
        }
        output.append('"');
    }

    private static final class Parser {
        private final String source;
        private int index;

        private Parser(String source) {
            this.source = source;
        }

        private int position() {
            return index;
        }

        private boolean atEnd() {
            return index >= source.length();
        }

        private void skipWhitespace() {
            while (!atEnd() && Character.isWhitespace(source.charAt(index))) {
                index++;
            }
        }

        private Object readValue() {
            skipWhitespace();
            if (atEnd()) {
                throw error("Expected a JSON value");
            }
            char current = source.charAt(index);
            if (current == '{') return readObject();
            if (current == '[') return readArray();
            if (current == '"') return readString();
            if (current == 't') return readLiteral("true", Boolean.TRUE);
            if (current == 'f') return readLiteral("false", Boolean.FALSE);
            if (current == 'n') return readLiteral("null", null);
            if (current == '-' || Character.isDigit(current)) return readNumber();
            throw error("Unexpected character '" + current + "'");
        }

        private Map<String, Object> readObject() {
            LinkedHashMap<String, Object> result = new LinkedHashMap<String, Object>();
            expect('{');
            skipWhitespace();
            if (consume('}')) return result;
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                result.put(key, readValue());
                skipWhitespace();
                if (consume('}')) return result;
                expect(',');
            }
        }

        private List<Object> readArray() {
            ArrayList<Object> result = new ArrayList<Object>();
            expect('[');
            skipWhitespace();
            if (consume(']')) return result;
            while (true) {
                result.add(readValue());
                skipWhitespace();
                if (consume(']')) return result;
                expect(',');
            }
        }

        private String readString() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (!atEnd()) {
                char current = source.charAt(index++);
                if (current == '"') return result.toString();
                if (current != '\\') {
                    result.append(current);
                    continue;
                }
                if (atEnd()) throw error("Incomplete escape sequence");
                char escaped = source.charAt(index++);
                switch (escaped) {
                    case '"': result.append('"'); break;
                    case '\\': result.append('\\'); break;
                    case '/': result.append('/'); break;
                    case 'b': result.append('\b'); break;
                    case 'f': result.append('\f'); break;
                    case 'n': result.append('\n'); break;
                    case 'r': result.append('\r'); break;
                    case 't': result.append('\t'); break;
                    case 'u':
                        if (index + 4 > source.length()) throw error("Incomplete Unicode escape");
                        result.append((char) Integer.parseInt(source.substring(index, index + 4), 16));
                        index += 4;
                        break;
                    default: throw error("Invalid escape sequence");
                }
            }
            throw error("Unterminated string");
        }

        private Object readLiteral(String literal, Object value) {
            if (!source.regionMatches(index, literal, 0, literal.length())) {
                throw error("Invalid JSON literal");
            }
            index += literal.length();
            return value;
        }

        private Number readNumber() {
            int start = index;
            if (source.charAt(index) == '-') index++;
            while (!atEnd() && Character.isDigit(source.charAt(index))) index++;
            boolean decimal = false;
            if (!atEnd() && source.charAt(index) == '.') {
                decimal = true;
                index++;
                while (!atEnd() && Character.isDigit(source.charAt(index))) index++;
            }
            if (!atEnd() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (!atEnd() && (source.charAt(index) == '+' || source.charAt(index) == '-')) index++;
                while (!atEnd() && Character.isDigit(source.charAt(index))) index++;
            }
            String number = source.substring(start, index);
            return decimal ? Double.valueOf(number) : Long.valueOf(number);
        }

        private boolean consume(char expected) {
            if (!atEnd() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!consume(expected)) {
                throw error("Expected '" + expected + "'");
            }
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at position " + index + ".");
        }
    }
}
