package searchfetch

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const defaultBaseURL = "http://localhost:8000"

// APIError is a structured error returned by the Search & Fetch API.
type APIError struct {
	StatusCode int
	Message    string
	Type       string
	Param      *string
	Code       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("search/fetch: HTTP %d: %s", e.StatusCode, e.Message)
}

// SearchResult is one ranked search result.
type SearchResult struct {
	RerankScore float64 `json:"rerank_score"`
	Title       string  `json:"title"`
	URL         string  `json:"url"`
	Snippet     string  `json:"snippet"`
	Summary     string  `json:"summary"`
	Content     string  `json:"content"`
	SiteName    *string `json:"site_name"`
	SiteIcon    *string `json:"site_icon"`
	Date        *string `json:"date"`
	Authority   int     `json:"authority"`
}

// FetchPage is the Markdown representation of a fetched page.
type FetchPage struct {
	Title   string `json:"title"`
	Content string `json:"content"`
}

// SearchOptions controls a Search request. A zero Count defaults to 10.
type SearchOptions struct {
	Count        int
	OriQuestion  *string
	Localization string
	NeedContent  bool
}

// Client calls the Search & Fetch API.
type Client struct {
	BaseURL    string
	APIKey     string
	HTTPClient *http.Client
	MaxRetries int
}

// New creates a client using the supplied API key.
func New(baseURL, apiKey string) *Client {
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	return &Client{
		BaseURL:    strings.TrimRight(baseURL, "/"),
		APIKey:     apiKey,
		HTTPClient: &http.Client{Timeout: 30 * time.Second},
		MaxRetries: 3,
	}
}

// Search performs a web search.
func (c *Client) Search(ctx context.Context, query string, options SearchOptions) ([]SearchResult, error) {
	count := options.Count
	if count == 0 {
		count = 10
	}
	payload := map[string]any{
		"query":        query,
		"count":        count,
		"localization": options.Localization,
		"need_content": options.NeedContent,
	}
	if options.OriQuestion != nil {
		payload["ori_question"] = *options.OriQuestion
	}
	body, err := c.do(ctx, "/v1/tools/search", payload)
	if err != nil {
		return nil, err
	}
	var response struct {
		Results []SearchResult `json:"results"`
	}
	if err := json.Unmarshal(body, &response); err != nil {
		return nil, fmt.Errorf("search/fetch: decode search response: %w", err)
	}
	return response.Results, nil
}

// Fetch downloads a URL and converts it to Markdown.
func (c *Client) Fetch(ctx context.Context, url string) (FetchPage, error) {
	body, err := c.do(ctx, "/v1/tools/fetch", map[string]any{"url": url})
	if err != nil {
		return FetchPage{}, err
	}
	var response FetchPage
	if err := json.Unmarshal(body, &response); err != nil {
		return FetchPage{}, fmt.Errorf("search/fetch: decode fetch response: %w", err)
	}
	return response, nil
}

func (c *Client) do(ctx context.Context, path string, payload any) ([]byte, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("search/fetch: encode request: %w", err)
	}
	client := c.HTTPClient
	if client == nil {
		client = http.DefaultClient
	}
	maxRetries := c.MaxRetries
	if maxRetries < 0 {
		maxRetries = 0
	}

	for attempt := 0; attempt <= maxRetries; attempt++ {
		request, err := http.NewRequestWithContext(
			ctx,
			http.MethodPost,
			c.BaseURL+path,
			bytes.NewReader(encoded),
		)
		if err != nil {
			return nil, fmt.Errorf("search/fetch: create request: %w", err)
		}
		request.Header.Set("Authorization", "Bearer "+c.APIKey)
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Accept", "application/json")

		response, err := client.Do(request)
		if err != nil {
			if attempt == maxRetries {
				return nil, fmt.Errorf("search/fetch: request failed: %w", err)
			}
			if err := sleepBeforeRetry(ctx, attempt, ""); err != nil {
				return nil, err
			}
			continue
		}
		responseBody, readErr := io.ReadAll(response.Body)
		response.Body.Close()
		if readErr != nil {
			if attempt == maxRetries {
				return nil, fmt.Errorf("search/fetch: read response: %w", readErr)
			}
			if err := sleepBeforeRetry(ctx, attempt, ""); err != nil {
				return nil, err
			}
			continue
		}

		if shouldRetry(response.StatusCode) && attempt < maxRetries {
			if err := sleepBeforeRetry(ctx, attempt, response.Header.Get("Retry-After")); err != nil {
				return nil, err
			}
			continue
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return nil, decodeAPIError(response.StatusCode, responseBody)
		}
		return responseBody, nil
	}
	return nil, errors.New("search/fetch: request failed")
}

func shouldRetry(status int) bool {
	return status == http.StatusRequestTimeout || status == http.StatusTooManyRequests || status >= 500
}

func decodeAPIError(status int, body []byte) error {
	apiError := &APIError{StatusCode: status, Message: fmt.Sprintf("HTTP %d", status), Type: "invalid_request_error"}
	var envelope struct {
		Error struct {
			Message string  `json:"message"`
			Type    string  `json:"type"`
			Param   *string `json:"param"`
			Code    string  `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &envelope); err == nil {
		if envelope.Error.Message != "" {
			apiError.Message = envelope.Error.Message
		}
		if envelope.Error.Type != "" {
			apiError.Type = envelope.Error.Type
		}
		apiError.Param = envelope.Error.Param
		apiError.Code = envelope.Error.Code
	}
	return apiError
}

func sleepBeforeRetry(ctx context.Context, attempt int, retryAfter string) error {
	delay := math.Min(8, 0.5*math.Pow(2, float64(attempt)))
	if retryAfter != "" {
		if seconds, err := strconv.ParseFloat(retryAfter, 64); err == nil {
			delay = math.Max(delay, seconds)
		} else if retryTime, err := http.ParseTime(retryAfter); err == nil {
			delay = math.Max(delay, retryTime.Sub(time.Now()).Seconds())
		}
	}
	if delay > 30 {
		delay = 30
	}
	delay += rand.Float64() * 0.1
	timer := time.NewTimer(time.Duration(delay * float64(time.Second)))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
